"""Real socket tests: HTTP, policy reload, and independent source-IP limits."""
from contextlib import closing
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import yaml
from proxy.policy import PolicyEngine
from proxy.protection import Protection
with patch('proxy.logger.get_logger', return_value=logging.getLogger('http-tests')):
    from proxy.proxy import serve_listener


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        if getattr(self.server, 'raw_response', None):
            self.connection.sendall(self.server.raw_response)
            self.close_connection = True
            return
        body = self.server.body
        self.server.calls += 1
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.server.body)))
        self.end_headers()

    def log_message(self, *args):
        pass


class HTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'policy.yaml'
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.backend.body, self.backend.calls = b'Safe response', 0
        self.backend_port = self.backend.server_port
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.addCleanup(self.backend.server_close)
        self.addCleanup(self.backend.shutdown)
        self.write_policy()
        self.policy = PolicyEngine(self.path)
        self.guard = Protection(self.policy)
        self.stop = threading.Event()
        ports = queue.Queue()
        self.thread = threading.Thread(target=serve_listener, args=(
            '127.0.0.1', 0, self.policy, '127.0.0.1', self.guard),
            kwargs={'http': True, 'ready': ports.put, 'stop': self.stop}, daemon=True)
        self.thread.start()
        self.port = ports.get(timeout=3)
        self.addCleanup(self.close_listener)

    def close_listener(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def write_policy(self, action='allow', mode='block', **limits):
        data = {'default_action': 'deny', 'inspection': {'on_sensitive': mode},
                'rules': [{'service': 'web', 'port': self.backend_port, 'action': action}],
                'protection': limits}
        new = self.path.with_suffix('.new')
        new.write_text(yaml.safe_dump(data))
        new.replace(self.path)

    def request(self, source='127.0.0.1', target=None, method='GET', headers=None):
        url = target or f'http://127.0.0.1:{self.backend_port}/demo'
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3,
                                                source_address=(source, 0))
        with closing(connection):
            connection.request(method, url, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())

    def raw(self, data):
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as conn:
            conn.sendall(data)
            chunks = []
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)

    def test_policy_hot_reload_and_inspection(self):
        self.assertEqual(self.request()[:2], (200, b'Safe response'))
        self.write_policy('deny')
        self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.backend.calls, 1)
        self.backend.body = b'password=fake-secret'
        self.write_policy('allow')
        self.assertIn(b'fake-secret', self.request()[1])
        self.write_policy('auto')
        self.assertEqual(self.request()[0], 403)
        self.write_policy('auto', 'redact')
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b'[REDACTED]', body)
        self.assertNotIn(b'fake-secret', body)
        self.path.write_text('rules: [')
        self.assertIn(b'[REDACTED]', self.request()[1])

    def test_busy_client_does_not_block_other_source(self):
        self.write_policy(requests_per_window=2, block_after_violations=2)
        self.assertEqual(self.request('127.0.0.2')[0], 200)
        self.assertEqual(self.request('127.0.0.2')[0], 200)
        self.assertEqual(self.request('127.0.0.2')[0], 429)
        self.assertEqual(self.request('127.0.0.2')[0], 429)
        self.assertEqual(self.request('127.0.0.1')[0], 200)
        self.assertIn(b'temporarily blocked', self.request('127.0.0.2')[1])

    def test_destination_validation_and_http_framing(self):
        self.assertEqual(self.request(target='http://example.com/')[0], 403)
        self.assertEqual(self.request(method='CONNECT')[0], 405)
        self.assertIn(b'400 Bad Request', self.raw(b'GET http://127.0.0.1/ HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n'))
        self.assertIn(b'400 Bad Request', self.raw(b'GET http://127.0.0.1/ HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n'))

    def test_head_and_header_limit(self):
        status, body, headers = self.request(method='HEAD')
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(headers['Content-Length'], str(len(self.backend.body)))
        self.write_policy(max_header_bytes=128)
        self.assertEqual(self.request(headers={'X-Large': 'x' * 256})[0], 431)

    def test_oversized_backend_and_invalid_protection_keeps_last_policy(self):
        self.backend.body = b'x' * (1024 * 1024 + 1)
        self.assertEqual(self.request()[0], 502)
        self.backend.body = b'Safe again'
        self.write_policy(requests_per_window=0)
        self.assertEqual(self.request()[:2], (200, b'Safe again'))

    def test_upstream_framing_and_bodyless_status(self):
        cases = [
            (b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nshort', 502, b'Truncated'),
            (b'HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nx', 502, b'Ambiguous'),
            (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nsafe\r\n0\r\n\r\n', 200, b'safe'),
            (b'HTTP/1.1 204 No Content\r\n\r\n', 204, b''),
        ]
        for raw, status, expected in cases:
            with self.subTest(raw=raw):
                self.backend.raw_response = raw
                actual, body, headers = self.request()
                self.assertEqual(actual, status)
                self.assertIn(expected, body)
                if status == 204:
                    self.assertNotIn('Content-Length', headers)

    def test_auto_refuses_partial_content(self):
        self.write_policy('auto')
        self.backend.raw_response = (b'HTTP/1.1 206 Partial Content\r\n'
                                     b'Content-Length: 4\r\nContent-Range: bytes 0-3/100\r\n\r\nsafe')
        self.assertEqual(self.request()[0], 502)

    def test_explicit_zero_port_is_not_default_port(self):
        self.assertEqual(self.request(target='http://127.0.0.1:0/')[0], 403)
        self.assertEqual(self.backend.calls, 0)

    def test_shared_listener_connection_limit_and_shutdown(self):
        self.write_policy(max_connections_per_client=1)
        tcp_ports = queue.Queue()
        tcp = threading.Thread(target=serve_listener, args=(
            '127.0.0.1', 0, self.policy, '127.0.0.1', self.guard),
            kwargs={'ready': tcp_ports.put, 'stop': self.stop})
        tcp.start()
        self.addCleanup(lambda: tcp.join(timeout=3))
        self.addCleanup(self.stop.set)
        with socket.create_connection(('127.0.0.1', tcp_ports.get(timeout=3))) as held:
            deadline = time.monotonic() + 2
            while self.guard.active != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.guard.active, 1)
            self.assertIn(b'concurrent', self.request()[1])
            self.assertEqual(self.request(source='127.0.0.2')[0], 200)
            self.stop.set()
            tcp.join(timeout=3)
            self.assertFalse(tcp.is_alive())
            self.assertEqual(held.recv(1), b'')
        self.thread.join(timeout=3)
        self.assertEqual(self.guard.active, 0)

    def test_backend_timeout_does_not_penalize_client(self):
        with patch('proxy.http_proxy.http.client.HTTPConnection.getresponse', side_effect=socket.timeout):
            response = self.raw(f'GET http://127.0.0.1:{self.backend_port}/ HTTP/1.1\r\nHost: local\r\n\r\n'.encode())
        self.assertIn(b'504 Gateway Timeout', response)
        self.assertEqual(self.guard.clients['127.0.0.1'].violations, 0)

    def test_incomplete_request_timeout(self):
        self.write_policy(request_timeout_seconds=1)
        self.assertIn(b'504 Gateway Timeout', self.raw(b'GET '))

    def test_strict_yaml_keeps_previous_policy(self):
        for invalid in [
            'default_action: deny\ndefault_action: allow',
            'rules: [{service: web, port: true, action: deny}]',
            'rules: [{service: web, port: 8001.5, action: deny}]',
            'inspection: {on_sensitve: redact}',
            'rules: [{service: web, port: 8001, action: allow}, {service: web, port: 8001, action: deny}]',
            'protetcion: {}',
        ]:
            with self.subTest(invalid=invalid):
                self.path.write_text(invalid)
                self.assertEqual(self.request()[0], 200)


class ProtectionTests(unittest.TestCase):
    def test_expiry_concurrency_and_global_limits(self):
        class Policy:
            def snapshot(self):
                from proxy.protection import parse_settings
                return {}, {'protection': parse_settings({
                    'requests_per_window': 2, 'window_seconds': 10,
                    'max_connections_per_client': 1, 'max_connections_total': 2,
                    'block_after_violations': 2, 'block_seconds': 30})}
        now = [0]
        guard = Protection(Policy(), clock=lambda: now[0])
        self.assertIsNone(guard.admit('a'))
        self.assertIn('concurrent', guard.admit('a'))
        self.assertIsNone(guard.admit('b'))
        self.assertIn('capacity', guard.admit('c'))
        guard.release('a')
        self.assertIsNone(guard.admit('a'))
        guard.release('a')
        self.assertIn('rate', guard.admit('a'))
        self.assertIn('blocked', guard.admit('a'))
        now[0] = 31
        self.assertIsNone(guard.admit('a'))
        guard.release('a')
        guard.release('b')
        self.assertEqual(guard.active, 0)

    def test_invalid_settings(self):
        from proxy.protection import parse_settings
        for value in ({'unknown': 1}, {'window_seconds': True}, {'block_seconds': -1}):
            with self.assertRaises(ValueError):
                parse_settings(value)


if __name__ == '__main__':
    unittest.main()

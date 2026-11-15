"""Exercise the repository's actual demo responder and original client."""
from contextlib import closing
import http.client
import logging
from pathlib import Path
import queue
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from client.client import send_request
from proxy.policy import PolicyEngine
from proxy.protection import Protection
with patch('proxy.logger.get_logger', return_value=logging.getLogger('demo-tests')):
    from proxy.proxy import serve_listener
from server.server import handle_client


class DemoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        handle_client(self.request, self.client_address, 8001)


class DemoServiceTests(unittest.TestCase):
    def test_original_client_and_http_use_real_demo_responder(self):
        with tempfile.TemporaryDirectory() as directory:
            with socketserver.ThreadingTCPServer(('127.0.0.1', 0), DemoHandler) as backend:
                thread = threading.Thread(target=backend.serve_forever)
                thread.start()
                stop = threading.Event()
                listeners = []
                try:
                    path = Path(directory) / 'policy.yaml'
                    backend_port = backend.server_address[1]
                    path.write_text(yaml.safe_dump({'rules': [
                        {'service': 'web', 'port': backend_port, 'action': 'auto'}]}))
                    policy = PolicyEngine(path)
                    guard = Protection(policy)
                    ports = []
                    for is_http in (False, True):
                        ready = queue.Queue()
                        listener = threading.Thread(target=serve_listener, args=(
                            '127.0.0.1', 0, policy, '127.0.0.1', guard),
                            kwargs={'http': is_http, 'ready': ready.put, 'stop': stop})
                        listeners.append(listener)
                        listener.start()
                        ports.append(ready.get(timeout=3))
                    _, response = send_request('127.0.0.1', ports[0], 'web', backend_port)
                    self.assertIn('Web service: request received successfully.', response)
                    self.assertIn('Received: service=web;', response)
                    for method in ('GET', 'HEAD'):
                        with closing(http.client.HTTPConnection('127.0.0.1', ports[1], timeout=3)) as client:
                            client.request(method, f'http://127.0.0.1:{backend_port}/')
                            result = client.getresponse()
                            self.assertEqual(result.status, 200)
                            body = result.read()
                            self.assertEqual(bool(body), method == 'GET')
                    # The real responder must read fragmented HTTP headers.
                    with socket.create_connection(backend.server_address, timeout=3) as client:
                        client.sendall(b'GET / HTTP/1.1\r\n')
                        client.sendall(b'Host: local\r\n\r\n')
                        self.assertIn(b'HTTP/1.1 200 OK', client.recv(4096))
                finally:
                    stop.set()
                    for listener in listeners:
                        listener.join(timeout=3)
                        self.assertFalse(listener.is_alive())
                    backend.shutdown()
                    thread.join(timeout=3)

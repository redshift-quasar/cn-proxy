"""Verify application behavior against a real local TCP backend."""

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from proxy.policy import PolicyEngine

# Keep tests from appending to the team's tracked production log.
with patch("proxy.logger.get_logger", return_value=logging.getLogger("proxy-tests")):
    from proxy.proxy import handle_client


class Backend(socketserver.BaseRequestHandler):
    def handle(self):
        data = b""
        while b"\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            data += chunk
        self.request.sendall(self.server.response)


class ProxyIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policies.yaml"
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Backend)
        self.server.daemon_threads = True
        self.server.response = b"Safe backend response\n"
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.stop_backend)
        self.write_policy("allow")
        self.policy = PolicyEngine(self.path)

    def stop_backend(self):
        self.server.shutdown()
        self.server.server_close()

    def write_policy(self, action, mode="block"):
        temporary = self.path.with_suffix(".new")
        temporary.write_text(yaml.safe_dump({
            "default_action": "deny", "inspection": {"on_sensitive": mode},
            "rules": [{"service": "web", "port": self.port, "action": action}],
        }), encoding="utf-8")
        temporary.replace(self.path)

    def request(self, raw=None):
        client, accepted = socket.socketpair()
        thread = threading.Thread(target=handle_client,
                                  args=(accepted, ("127.0.0.1", 12345),
                                        self.policy, "127.0.0.1"), daemon=True)
        thread.start()
        with client:
            client.settimeout(3)
            client.sendall(raw or f"service=web;port={self.port}\n".encode())
            result = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                result += chunk
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        return result.decode()

    def test_allow_deny_allow_hot_reload(self):
        self.assertIn("Safe backend response", self.request())
        self.write_policy("deny")
        self.assertIn("DENIED:", self.request())
        self.write_policy("allow")
        self.assertIn("Safe backend response", self.request())

    def test_auto_block_redact_and_invalid_yaml_retains_last_policy(self):
        self.server.response = b"Demo response password=fake-test-password\n"
        self.write_policy("auto")
        self.assertIn("BLOCKED:", self.request())
        self.write_policy("auto", mode="redact")
        response = self.request()
        self.assertIn("[REDACTED]", response)
        self.assertNotIn("fake-test-password", response)
        self.path.write_text("rules: [", encoding="utf-8")
        self.assertIn("[REDACTED]", self.request())

    def test_concurrent_clients_and_invalid_request(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: self.request(), range(8)))
        self.assertTrue(all("Safe backend response" in response for response in responses))
        self.assertIn("ERROR:", self.request(b"service=web;port=nope\n"))
        self.assertIn("DENIED:", self.request(f"service=unknown;port={self.port}\n".encode()))


if __name__ == "__main__":
    unittest.main()

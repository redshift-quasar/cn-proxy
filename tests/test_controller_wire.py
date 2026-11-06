"""Exercise the actual controller process through a local OpenFlow connection."""

from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

from os_ken.ofproto import ofproto_v1_3 as ofp
from os_ken.ofproto import ofproto_v1_3_parser as parser


ROOT = Path(__file__).resolve().parents[1]


class ControllerWireTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policies.yaml"
        self.write_policy("allow")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.log = tempfile.TemporaryFile()
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "controller.run_controller",
             "--policy", str(self.path), "--ofp-port", str(port),
             "--poll-interval", "0.05"],
            cwd=str(ROOT), stdout=self.log, stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.stop_controller)
        deadline = time.monotonic() + 8
        while True:
            if self.process.poll() is not None:
                self.log.seek(0)
                self.fail(self.log.read().decode())
            try:
                self.connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.fail("Controller listener did not start")
                time.sleep(0.05)
        self.addCleanup(self.connection.close)
        self.connection.settimeout(5)
        self.dp = SimpleNamespace(ofproto=ofp, ofproto_parser=parser)
        self.handshake()

    def stop_controller(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def write_policy(self, action, default="deny"):
        temporary = self.path.with_suffix(".new")
        temporary.write_text(
            f"default_action: {default}\nrules:\n"
            f"  - {{service: database, port: 8002, action: {action}}}\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def send(self, kind, body=b"", xid=1):
        self.connection.sendall(struct.pack("!BBHI", 4, kind, 8 + len(body), xid) + body)

    def receive(self):
        def exact(size):
            data = b""
            while len(data) < size:
                chunk = self.connection.recv(size - len(data))
                if not chunk:
                    self.fail("Controller closed the OpenFlow connection")
                data += chunk
            return data
        header = exact(8)
        version, kind, length, xid = struct.unpack("!BBHI", header)
        data = bytearray(header + exact(length - 8))
        # These controller -> switch types have no registered decoder in
        # OS-Ken. Their headers are sufficient for our switch handshake.
        header_only = {
            ofp.OFPT_FEATURES_REQUEST: parser.OFPFeaturesRequest,
            ofp.OFPT_SET_CONFIG: parser.OFPSetConfig,
            ofp.OFPT_BARRIER_REQUEST: parser.OFPBarrierRequest,
        }
        if kind == ofp.OFPT_MULTIPART_REQUEST:
            subtype, = struct.unpack_from("!H", data, 8)
            self.assertEqual(subtype, ofp.OFPMP_PORT_DESC)
            self.send(ofp.OFPT_MULTIPART_REPLY,
                      struct.pack("!HH4x", subtype, 0), xid)
            message = SimpleNamespace(msg_type=kind, xid=xid)
        elif kind in header_only:
            message = header_only[kind](self.dp)
            message.xid = xid
        else:
            message = parser.msg_parser(self.dp, version, kind, length, xid, data)
        if kind == ofp.OFPT_ECHO_REQUEST:
            self.send(ofp.OFPT_ECHO_REPLY, bytes(message.data), xid)
        return message

    def until_barrier(self):
        messages = []
        while True:
            message = self.receive()
            messages.append(message)
            if isinstance(message, parser.OFPBarrierRequest):
                self.send(ofp.OFPT_BARRIER_REPLY, xid=message.xid)
                return messages

    def handshake(self):
        self.assertIsInstance(self.receive(), parser.OFPHello)
        self.send(ofp.OFPT_HELLO)
        request = self.receive()
        self.assertIsInstance(request, parser.OFPFeaturesRequest)
        body = struct.pack(ofp.OFP_SWITCH_FEATURES_PACK_STR, 1, 0, 2, 0, 0, 0)
        self.send(ofp.OFPT_FEATURES_REPLY, body, request.xid)
        messages = self.until_barrier()
        self.assertTrue(any(isinstance(msg, parser.OFPFlowMod)
                            and msg.match.get("tcp_dst") == 8002
                            and msg.command == ofp.OFPFC_ADD for msg in messages))

    def test_live_polling_sends_allow_deny_allow_without_requests(self):
        self.write_policy("deny")
        messages = self.until_barrier()
        self.assertTrue(any(isinstance(msg, parser.OFPFlowMod)
                            and msg.command == ofp.OFPFC_DELETE_STRICT
                            and msg.match.get("tcp_dst") == 8002 for msg in messages))
        self.write_policy("allow")
        messages = self.until_barrier()
        self.assertTrue(any(isinstance(msg, parser.OFPFlowMod)
                            and msg.command == ofp.OFPFC_ADD
                            and msg.match.get("tcp_dst") == 8002 for msg in messages))
        self.assertIsNone(self.process.poll())

    def test_live_default_only_change_sends_broad_proxy_permit(self):
        self.write_policy("allow", default="auto")
        messages = self.until_barrier()
        self.assertTrue(any(isinstance(msg, parser.OFPFlowMod)
                            and msg.command == ofp.OFPFC_ADD
                            and msg.priority == 150
                            and msg.match.get("ipv4_src") == "10.0.0.2"
                            and "tcp_dst" not in dict(msg.match.items())
                            for msg in messages))

    def test_sigint_stops_controller_without_native_thread_cleanup_error(self):
        self.process.send_signal(signal.SIGINT)
        self.assertIn(self.process.wait(timeout=5), (-signal.SIGINT, 0))
        self.log.seek(0)
        self.assertNotIn(b"AttributeError", self.log.read())


if __name__ == "__main__":
    unittest.main()

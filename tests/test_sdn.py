"""Controller tests use real OS-Ken OpenFlow encoders, without root or OVS."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
from os_ken.lib.packet import ethernet, ether_types, ipv4, packet, tcp
from os_ken.ofproto import ofproto_v1_3 as ofp
from os_ken.ofproto import ofproto_v1_3_parser as parser

from controller.flow_manager import (
    COOKIE_MASK, FlowManager, LEARNING_COOKIE, LEARNING_TABLE,
    NetworkPolicy, POLICY_COOKIE, POLICY_TABLE,
)
from controller.simple_switch import SimpleSwitch13
from proxy.policy import PolicyEngine


class Datapath:
    """Encode real messages and model the exact-match fields used by this lab."""
    ofproto = ofp
    ofproto_parser = parser

    def __init__(self, dpid=1):
        self.id = dpid
        self.messages = []
        self.flows = {}

    def send_msg(self, message):
        message.serialize()  # catches bad OpenFlow fields/instructions
        self.messages.append(message)
        if not isinstance(message, parser.OFPFlowMod):
            return
        key = (message.table_id, message.priority, tuple(message.match.items()))
        if message.command == ofp.OFPFC_ADD:
            self.flows[key] = message
        elif message.command in {ofp.OFPFC_DELETE, ofp.OFPFC_DELETE_STRICT}:
            for candidate, flow in list(self.flows.items()):
                if message.table_id not in {ofp.OFPTT_ALL, flow.table_id}:
                    continue
                if flow.cookie & message.cookie_mask != message.cookie & message.cookie_mask:
                    continue
                if message.command == ofp.OFPFC_DELETE_STRICT and candidate != key:
                    continue
                if not all(flow.match.get(k) == v for k, v in message.match.items()):
                    continue
                del self.flows[candidate]

    def route(self, fields, table=POLICY_TABLE):
        candidates = [flow for flow in self.flows.values()
                      if flow.table_id == table and all(
                          fields.get(k) == v for k, v in flow.match.items())]
        if not candidates:
            return "DROP"
        flow = max(candidates, key=lambda entry: entry.priority)
        for instruction in flow.instructions:
            if isinstance(instruction, parser.OFPInstructionGotoTable):
                return self.route(fields, instruction.table_id)
            for action in getattr(instruction, "actions", []):
                if isinstance(action, parser.OFPActionOutput):
                    return action.port
        return "DROP"


def backend_packet(port=8002, source="10.0.0.2"):
    return dict(eth_type=0x0800, ip_proto=6, ipv4_src=source,
                ipv4_dst="10.0.0.3", tcp_dst=port,
                in_port=2, eth_src="00:00:00:00:00:02",
                eth_dst="00:00:00:00:00:03")


class PolicyFixture(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "policies.yaml"
        self.write_policy()

    def write_policy(self, action="allow", default="deny", extra=None):
        rules = [{"service": "database", "port": 8002, "action": action}]
        rules.extend(extra or [])
        self.write_text(yaml.safe_dump(dict(default_action=default, rules=rules)))

    def write_text(self, content):
        # Atomic replacement matches an editor save and ensures mtime changes.
        previous = self.path.stat().st_mtime_ns if self.path.exists() else 0
        temporary = self.path.with_suffix(".new")
        temporary.write_text(content, encoding="utf-8")
        os.utime(temporary, ns=(previous + 1_000_000, previous + 1_000_000))
        temporary.replace(self.path)


class SnapshotTests(PolicyFixture):
    def test_runtime_allow_deny_allow_and_listener(self):
        engine = PolicyEngine(self.path)
        changes = []
        engine.add_listener(lambda *args: changes.append(args))
        self.assertEqual(engine.evaluate("database", 8002), "ALLOW")
        self.write_policy("deny")
        self.assertEqual(engine.snapshot()[0][("database", 8002)], "DENY")
        self.write_policy("allow")
        self.assertEqual(engine.evaluate("database", 8002), "ALLOW")
        self.assertEqual(changes, [("database", 8002, "ALLOW", "DENY"),
                                   ("database", 8002, "DENY", "ALLOW")])

    def test_default_changes_reload_without_requests_and_copies_are_isolated(self):
        engine = PolicyEngine(self.path)
        self.write_policy(default="auto")
        rules, settings = engine.snapshot()
        self.assertEqual(settings["default_action"], "AUTO")
        rules.clear()
        settings["default_action"] = "DENY"
        self.assertEqual(engine.evaluate("unknown", 5000), "AUTO")
        self.assertEqual(engine.evaluate("database", 8002), "ALLOW")

    def test_invalid_or_missing_policy_keeps_last_valid_snapshot(self):
        engine = PolicyEngine(self.path)
        initial = engine.snapshot()
        for content in ("rules: [", "rules: {}", "default_action: typo"):
            self.write_text(content)
            self.assertEqual(engine.snapshot(), initial)
        self.path.unlink()
        self.assertEqual(engine.snapshot(), initial)
        self.write_policy("deny")
        self.assertEqual(engine.evaluate("database", 8002), "DENY")

    def test_initial_invalid_policy_fails(self):
        self.write_text("rules: {}")
        with self.assertRaises(ValueError):
            PolicyEngine(self.path)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.dp = Datapath()
        self.manager = FlowManager("10.0.0.2", "10.0.0.3")
        self.allow = NetworkPolicy(False, frozenset({8001, 8002}))
        self.deny = NetworkPolicy(False, frozenset({8001}))
        self.manager.initialize(self.dp, self.allow)

    def test_allow_deny_allow_overrides_an_existing_learning_flow(self):
        self.manager._add(self.dp, LEARNING_TABLE, 10,
                          parser.OFPMatch(in_port=2, eth_dst="00:00:00:00:00:03"),
                          [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                                       [parser.OFPActionOutput(3)])],
                          LEARNING_COOKIE)
        self.assertEqual(self.dp.route(backend_packet()), 3)
        self.manager.update(self.dp, self.allow, self.deny)
        self.assertEqual(self.dp.route(backend_packet()), "DROP")
        self.assertEqual(self.dp.route(backend_packet(8001)), 3)
        self.manager.update(self.dp, self.deny, self.allow)
        self.assertEqual(self.dp.route(backend_packet()), 3)

    def test_direct_backend_and_unknown_ports_blocked_but_arp_and_icmp_work(self):
        self.assertEqual(self.dp.route(backend_packet(source="10.0.0.1")), "DROP")
        self.assertEqual(self.dp.route(backend_packet(9999)), "DROP")
        self.assertEqual(self.dp.route(dict(eth_type=0x0806)), ofp.OFPP_CONTROLLER)
        self.assertEqual(self.dp.route(dict(eth_type=0x0800, ip_proto=1,
                                           ipv4_dst="10.0.0.3")), ofp.OFPP_CONTROLLER)
        self.assertEqual(self.dp.route(dict(eth_type=0x0800, ip_proto=6,
                                           ipv4_dst="10.0.0.2", tcp_dst=9000)),
                         ofp.OFPP_CONTROLLER)

    def test_default_allow_to_deny_and_back(self):
        general = NetworkPolicy(True, frozenset())
        self.manager.update(self.dp, self.allow, general)
        self.assertEqual(self.dp.route(backend_packet(9999)), ofp.OFPP_CONTROLLER)
        self.assertEqual(self.dp.route(backend_packet(source="10.0.0.1")), "DROP")
        self.manager.update(self.dp, general, self.deny)
        self.assertEqual(self.dp.route(backend_packet(9999)), "DROP")
        self.assertEqual(self.dp.route(backend_packet(8001)), ofp.OFPP_CONTROLLER)

    def test_reconnect_removes_only_owned_cookies(self):
        self.manager._add(self.dp, LEARNING_TABLE, 50,
                          parser.OFPMatch(in_port=9), [], 1234)
        self.manager.initialize(self.dp, self.deny)
        self.assertEqual(self.dp.route(backend_packet()), "DROP")
        self.assertTrue(any(flow.cookie == 1234 for flow in self.dp.flows.values()))
        deletes = [msg for msg in self.dp.messages
                   if isinstance(msg, parser.OFPFlowMod)
                   and msg.command == ofp.OFPFC_DELETE]
        self.assertTrue(all(msg.cookie_mask == COOKIE_MASK for msg in deletes))

    def test_auto_and_shared_port_are_left_to_proxy_inspection(self):
        rules = {("database", 8002): "DENY", ("alias", 8002): "AUTO"}
        policy = NetworkPolicy.from_snapshot(rules, {"default_action": "DENY"})
        self.assertIn(8002, policy.allowed_ports)
        self.assertTrue(policy.permits("10.0.0.2", 8002, "10.0.0.2"))
        self.assertFalse(policy.permits("10.0.0.1", 8002, "10.0.0.2"))
        general = NetworkPolicy.from_snapshot(rules, {"default_action": "ALLOW"})
        self.assertTrue(general.permits("10.0.0.2", 8002, "10.0.0.2"))


class ControllerTests(PolicyFixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"CN_PROXY_POLICY": str(self.path),
                                     "CN_SDN_PROXY_IP": "10.0.0.2",
                                     "CN_SDN_SERVER_IP": "10.0.0.3",
                                     "CN_SDN_POLL_INTERVAL": "0.1"})
        env.start()
        self.addCleanup(env.stop)
        self.app = SimpleSwitch13()
        self.dp = Datapath()
        self.connect(self.dp)

    def connect(self, dp):
        self.app.switch_features_handler(SimpleNamespace(
            msg=SimpleNamespace(datapath=dp, n_tables=2)))

    def test_refresh_all_switches_without_proxy_requests(self):
        second = Datapath(2)
        self.connect(second)
        self.write_policy("deny")
        self.app.refresh_policy(None)
        for dp in (self.dp, second):
            self.assertEqual(dp.route(backend_packet()), "DROP")
        self.write_policy("allow")
        self.app.refresh_policy(None)
        self.assertEqual(second.route(backend_packet()), ofp.OFPP_CONTROLLER)

    def test_removed_rule_and_default_only_changes(self):
        self.write_text("default_action: deny\nrules: []\n")
        self.app.refresh_policy(None)
        self.assertEqual(self.dp.route(backend_packet()), "DROP")
        self.write_text("default_action: auto\nrules: []\n")
        self.app.refresh_policy(None)
        self.assertEqual(self.dp.route(backend_packet(4567)), ofp.OFPP_CONTROLLER)

    def test_invalid_update_keeps_flows_and_unchanged_policy_sends_nothing(self):
        before = len(self.dp.messages)
        self.app.refresh_policy(None)
        self.write_text("rules: [")
        self.app.refresh_policy(None)
        self.assertEqual(len(self.dp.messages), before)
        self.assertEqual(self.dp.route(backend_packet()), ofp.OFPP_CONTROLLER)

    def test_reconnect_clears_mac_learning_and_ignores_old_disconnect(self):
        self.app.mac_to_port[1]["old-mac"] = 9
        replacement = Datapath(1)
        self.connect(replacement)
        self.app.state_change_handler(SimpleNamespace(datapath=self.dp))
        self.assertIs(self.app.datapaths[1], replacement)
        self.assertEqual(self.app.mac_to_port[1], {})
        self.app.state_change_handler(SimpleNamespace(datapath=replacement))
        self.assertNotIn(1, self.app.datapaths)

    def packet_in(self, source="10.0.0.2", port=8002, ethertype=0x0800):
        data = packet.Packet()
        data.add_protocol(ethernet.ethernet(
            src="00:00:00:00:00:02", dst="00:00:00:00:00:03", ethertype=ethertype))
        if ethertype == 0x0800:
            data.add_protocol(ipv4.ipv4(src=source, dst="10.0.0.3", proto=6))
            data.add_protocol(tcp.tcp(src_port=12345, dst_port=port))
        data.serialize()
        self.app.packet_in_handler(SimpleNamespace(msg=SimpleNamespace(
            datapath=self.dp, match={"in_port": 2}, data=data.data,
            buffer_id=ofp.OFP_NO_BUFFER)))

    def test_queued_packet_in_cannot_bypass_deny_or_proxy_requirement(self):
        before = len(self.dp.messages)
        self.packet_in(source="10.0.0.1")
        self.assertEqual(len(self.dp.messages), before)
        self.write_policy("deny")
        self.app.refresh_policy(None)
        before = len(self.dp.messages)
        self.packet_in()
        self.assertEqual(len(self.dp.messages), before)

    def test_packet_learning_is_in_table_one_and_lldp_is_ignored(self):
        before = len(self.dp.messages)
        self.packet_in(ethertype=ether_types.ETH_TYPE_LLDP)
        self.assertEqual(len(self.dp.messages), before)
        self.app.mac_to_port[1]["00:00:00:00:00:03"] = 3
        self.packet_in()
        flow = self.dp.messages[-2]
        self.assertIsInstance(flow, parser.OFPFlowMod)
        self.assertEqual(flow.table_id, LEARNING_TABLE)
        self.assertEqual(flow.cookie, LEARNING_COOKIE)
        self.assertEqual(self.dp.messages[-1].actions[0].port, 3)

    def test_watcher_uses_event_queue_and_stops(self):
        def stop(_interval):
            self.app.is_active = False
        with patch("controller.simple_switch.hub.sleep", side_effect=stop), \
                patch.object(self.app, "send_event") as send:
            self.app._watch_policies()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

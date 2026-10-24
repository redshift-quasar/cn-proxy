"""Policy-aware OS-Ken learning switch for the CN Proxy Mininet lab."""

import ipaddress
import math
import os

from os_ken.base import app_manager
from os_ken.controller import event, ofp_event
from os_ken.controller.handler import (
    MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER, set_ev_cls,
)
from os_ken.lib import hub
from os_ken.lib.packet import ethernet, ether_types, ipv4, packet, tcp
from os_ken.ofproto import ofproto_v1_3

from controller.flow_manager import (
    FlowManager, NetworkPolicy, LEARNING_COOKIE, LEARNING_TABLE,
)
from proxy.policy import PolicyEngine


class EventPolicyRefresh(event.EventBase):
    """Serialize policy refreshes with switch and packet events."""


class SimpleSwitch13(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.proxy_ip = str(ipaddress.IPv4Address(
            os.environ.get("CN_SDN_PROXY_IP", "10.0.0.2")))
        self.server_ip = str(ipaddress.IPv4Address(
            os.environ.get("CN_SDN_SERVER_IP", "10.0.0.3")))
        self.poll_interval = float(os.environ.get("CN_SDN_POLL_INTERVAL", "1"))
        if not math.isfinite(self.poll_interval) or self.poll_interval <= 0:
            raise ValueError("Policy poll interval must be positive and finite")
        self.policy_engine = PolicyEngine(
            os.environ.get("CN_PROXY_POLICY"), logger=self.logger)
        self.policy = NetworkPolicy.from_snapshot(*self.policy_engine.snapshot())
        self.flow_manager = FlowManager(self.proxy_ip, self.server_ip)

    def start(self):
        super().start()
        self.threads.append(hub.spawn(self._watch_policies))

    def _watch_policies(self):
        while self.is_active:
            hub.sleep(self.poll_interval)
            if self.is_active:
                self.send_event(self.name, EventPolicyRefresh())

    @set_ev_cls(EventPolicyRefresh)
    def refresh_policy(self, _ev):
        policy = NetworkPolicy.from_snapshot(*self.policy_engine.snapshot())
        if policy == self.policy:
            return
        previous, self.policy = self.policy, policy
        for datapath in list(self.datapaths.values()):
            self.flow_manager.update(datapath, previous, policy)
        self.logger.info("POLICY_SENT | switches=%s | default_allowed=%s | ports=%s",
                         len(self.datapaths), policy.default_allowed,
                         sorted(policy.allowed_ports))

    def add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, table_id=LEARNING_TABLE,
            cookie=LEARNING_COOKIE, priority=priority, idle_timeout=60,
            match=match, instructions=[parser.OFPInstructionActions(
                datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)],
        ))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        if ev.msg.n_tables < 2:
            self.logger.error("Switch %s needs at least two flow tables", datapath.id)
            return
        self.refresh_policy(None)
        self.datapaths[datapath.id] = datapath
        self.mac_to_port[datapath.id] = {}
        self.flow_manager.initialize(datapath, self.policy)
        self.logger.info("Switch %s connected; current policy sent", datapath.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if self.datapaths.get(datapath.id) is datapath:
            self.datapaths.pop(datapath.id, None)
            self.mac_to_port.pop(datapath.id, None)
            self.logger.info("Switch %s disconnected", datapath.id)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_handler(self, ev):
        self.logger.error("OPENFLOW_ERROR | switch=%s | type=%s | code=%s",
                          ev.msg.datapath.id, ev.msg.type, ev.msg.code)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        self.logger.debug("Switch %s processed preceding flow messages",
                          ev.msg.datapath.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        if self.datapaths.get(datapath.id) is not datapath:
            return
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        ip = pkt.get_protocol(ipv4.ipv4)
        segment = pkt.get_protocol(tcp.tcp)
        # A queued PacketIn can outlive an ALLOW -> DENY change. PacketOut
        # would bypass table 0, so recheck before sending it directly to a port.
        if ip is not None and ip.dst == self.server_ip and ip.proto == 6:
            if segment is None or not self.policy.permits(
                    ip.src, segment.dst_port, self.proxy_ip):
                return

        learned = self.mac_to_port.setdefault(datapath.id, {})
        learned[eth.src] = in_port
        out_port = learned.get(eth.dst, ofproto.OFPP_FLOOD)
        if out_port == in_port:
            return
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofproto.OFPP_FLOOD:
            self.add_flow(datapath, 10, parser.OFPMatch(
                in_port=in_port, eth_src=eth.src, eth_dst=eth.dst), actions)
        datapath.send_msg(parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None,
        ))

"""OpenFlow 1.3 policy table, followed by a separate learning table."""

from dataclasses import dataclass


POLICY_TABLE = 0
LEARNING_TABLE = 1
POLICY_COOKIE = 0x434E000000000001
LEARNING_COOKIE = 0x434E000000000002
COOKIE_MASK = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class NetworkPolicy:
    default_allowed: bool
    allowed_ports: frozenset

    @classmethod
    def from_snapshot(cls, rules, settings):
        # OpenFlow cannot distinguish application service names sharing a port.
        # Allow the port if *any* service name (including an unknown name via
        # default_action) can use it. The proxy enforces the finer distinction.
        default_allowed = settings["default_action"] in {"ALLOW", "AUTO"}
        ports = frozenset(
            port for (_, port), action in rules.items()
            if action in {"ALLOW", "AUTO"}
        ) if not default_allowed else frozenset()
        return cls(default_allowed, ports)

    def permits(self, source_ip, destination_port, proxy_ip):
        return source_ip == proxy_ip and (
            self.default_allowed or destination_port in self.allowed_ports
        )


class FlowManager:
    def __init__(self, proxy_ip, server_ip):
        self.proxy_ip = proxy_ip
        self.server_ip = server_ip

    @staticmethod
    def _add(datapath, table, priority, match, instructions, cookie):
        datapath.send_msg(datapath.ofproto_parser.OFPFlowMod(
            datapath=datapath, table_id=table, priority=priority,
            cookie=cookie, match=match, instructions=instructions,
        ))

    def _permit(self, datapath, port=None, delete=False):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        fields = dict(eth_type=0x0800, ip_proto=6,
                      ipv4_src=self.proxy_ip, ipv4_dst=self.server_ip)
        if port is not None:
            fields["tcp_dst"] = port
        match = parser.OFPMatch(**fields)
        priority = 150 if port is None else 200
        if delete:
            datapath.send_msg(parser.OFPFlowMod(
                datapath=datapath, table_id=POLICY_TABLE,
                command=ofproto.OFPFC_DELETE_STRICT,
                cookie=POLICY_COOKIE, cookie_mask=COOKIE_MASK,
                priority=priority, match=match,
                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
            ))
        else:
            self._add(datapath, POLICY_TABLE, priority, match,
                      [parser.OFPInstructionGotoTable(LEARNING_TABLE)],
                      POLICY_COOKIE)

    def initialize(self, datapath, policy):
        """Clear only our cookies on reconnect and install the current policy."""
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        for cookie in (POLICY_COOKIE, LEARNING_COOKIE):
            datapath.send_msg(parser.OFPFlowMod(
                datapath=datapath, table_id=ofproto.OFPTT_ALL,
                command=ofproto.OFPFC_DELETE,
                cookie=cookie, cookie_mask=COOKIE_MASK,
                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
                match=parser.OFPMatch(),
            ))

        # Only the proxy may open backend TCP connections. ARP, ICMP, and
        # client -> proxy traffic still proceed to the learning table.
        self._add(datapath, POLICY_TABLE, 100,
                  parser.OFPMatch(eth_type=0x0800, ip_proto=6,
                                  ipv4_dst=self.server_ip),
                  [], POLICY_COOKIE)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                         ofproto.OFPCML_NO_BUFFER)]
        self._add(datapath, LEARNING_TABLE, 0, parser.OFPMatch(),
                  [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                actions)], LEARNING_COOKIE)
        self.update(datapath, NetworkPolicy(False, frozenset()), policy, barrier=False)
        # Install the general goto last: a partially initialized table drops.
        self._add(datapath, POLICY_TABLE, 0, parser.OFPMatch(),
                  [parser.OFPInstructionGotoTable(LEARNING_TABLE)],
                  POLICY_COOKIE)
        datapath.send_msg(parser.OFPBarrierRequest(datapath))

    def update(self, datapath, old, new, barrier=True):
        # Revoke first; never remove the backend default-drop rule. Strict
        # deletes preserve learning flows and unrelated controller entries.
        if old.default_allowed and not new.default_allowed:
            self._permit(datapath, delete=True)
        for port in sorted(old.allowed_ports - new.allowed_ports):
            self._permit(datapath, port, delete=True)
        if new.default_allowed and not old.default_allowed:
            self._permit(datapath)
        for port in sorted(new.allowed_ports - old.allowed_ports):
            self._permit(datapath, port)
        if barrier:
            datapath.send_msg(datapath.ofproto_parser.OFPBarrierRequest(datapath))

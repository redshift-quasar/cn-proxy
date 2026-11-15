from mininet.topo import Topo


class CNProxyTopo(Topo):
    """h1: client, h2: proxy, h3: all three simulated services."""

    def build(self, switch_name="s1", extra_clients=0):
        client = self.addHost("h1", ip="10.0.0.1/24")
        proxy = self.addHost("h2", ip="10.0.0.2/24")
        server = self.addHost("h3", ip="10.0.0.3/24")
        switch = self.addSwitch(switch_name, protocols="OpenFlow13",
                                failMode="secure", dpid="0000000000000001")
        self.addLink(client, switch)
        self.addLink(proxy, switch)
        self.addLink(server, switch)
        for number in range(4, 4 + extra_clients):
            host = self.addHost(f"h{number}", ip=f"10.0.0.{number}/24")
            self.addLink(host, switch)


topos = {"cnproxy": CNProxyTopo}

from mininet.topo import Topo

class CNProxyTopo(Topo):
    
    def build(self):
        client=self.addHost("h1", ip="10.0.0.1/24")
        proxy=self.addHost("h2", ip="10.0.0.2/24")
        server=self.addHost("h3", ip="10.0.0.3/24")
        
        switch=self.addSwitch("s1")
        
        self.addLink(client,switch)
        self.addLink(proxy,switch)
        self.addLink(server,switch)
        
    
topos= {
    'cnproxy':CNProxyTopo
}
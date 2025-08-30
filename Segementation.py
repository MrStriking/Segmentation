import os
import sys
import subprocess
import time

class NetworkManager:
    def __init__(self):
        self.bridges = ['br1', 'br2', 'br3']  
        self.vlans = {
            'HR': {'vlan': 10, 'subnet': '192.168.1.0/24', 'bridge': 'br1', 'gateway': '192.168.1.1'},
            'IT': {'vlan': 20, 'subnet': '192.168.2.0/24', 'bridge': 'br2', 'gateway': '192.168.2.1'},
            'Finance': {'vlan': 30, 'subnet': '192.168.3.0/24', 'bridge': 'br3', 'gateway': '192.168.3.1'}
        }
        self.hosts = {
            'hr1': {'dept': 'HR', 'ip': '192.168.1.10/24'},
            'hr2': {'dept': 'HR', 'ip': '192.168.1.11/24'},
            'it1': {'dept': 'IT', 'ip': '192.168.2.10/24'},
            'it2': {'dept': 'IT', 'ip': '192.168.2.11/24'},
            'fin1': {'dept': 'Finance', 'ip': '192.168.3.10/24'}, 
            'fin2': {'dept': 'Finance', 'ip': '192.168.3.11/24'}   
        }

    def run_cmd(self, cmd, check=True):
        print(f"Running: {cmd}")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result

    def create_namespace(self, ns):
        self.run_cmd(f"ip netns add {ns}")

    def create_veth_pair(self, veth1, veth2):
        result = self.run_cmd(f"ip link add {veth1} type veth peer name {veth2}")
        if result.returncode != 0:
            return False
        return True

    def create_bridge(self, bridge):
        self.run_cmd(f"ip link add name {bridge} type bridge")
        self.run_cmd(f"ip link set {bridge} up")

    def setup_network(self):
        print("Setting up network")
        for bridge in self.bridges:
            self.create_bridge(bridge)
      
        self.run_cmd("ip netns add router")
        router_interfaces = []
        for dept, config in self.vlans.items():
            vlan = config['vlan']
            bridge = config['bridge']
            gateway = config['gateway']
            veth_router = f"vr{vlan}"
            veth_br = f"vbr{vlan}"
            router_interfaces.append(veth_router)
            if self.create_veth_pair(veth_router, veth_br):
                self.run_cmd(f"ip link set {veth_router} netns router")
                self.run_cmd(f"ip link set {veth_br} master {bridge}")
                self.run_cmd(f"ip netns exec router ip addr add {gateway}/24 dev {veth_router}")
                self.run_cmd(f"ip netns exec router ip link set {veth_router} up")
                self.run_cmd(f"ip link set {veth_br} up")
        
        for host, config in self.hosts.items():
            dept = config['dept']
            vlan_config = self.vlans[dept]
            bridge = vlan_config['bridge']
            gateway = vlan_config['gateway']
            self.create_namespace(host)
            veth_host = f"v{host}"
            veth_br = f"vb{host}"
            if self.create_veth_pair(veth_host, veth_br):
                self.run_cmd(f"ip link set {veth_host} netns {host}")
                self.run_cmd(f"ip link set {veth_br} master {bridge}")
                self.run_cmd(f"ip netns exec {host} ip addr add {config['ip']} dev {veth_host}")
                self.run_cmd(f"ip netns exec {host} ip link set {veth_host} up")
                self.run_cmd(f"ip netns exec {host} ip link set lo up")
                self.run_cmd(f"ip netns exec {host} ip route add default via {gateway}")
                self.run_cmd(f"ip link set {veth_br} up")
        
        self.run_cmd("sysctl -w net.ipv4.ip_forward=1")
        self.run_cmd("ip netns exec router sysctl -w net.ipv4.ip_forward=1")
        self.setup_firewall_rules()
        self.verify_setup()

    def setup_firewall_rules(self):
        print("Firewall rules:")
        self.run_cmd("ip netns exec router iptables -F")
        self.run_cmd("ip netns exec router iptables -t nat -F")
        self.run_cmd("ip netns exec router iptables -X", check=False)
        self.run_cmd("ip netns exec router iptables -P FORWARD DROP")
        self.run_cmd("ip netns exec router iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")
        self.run_cmd("ip netns exec router iptables -A FORWARD -s 192.168.2.0/24 -j ACCEPT")
        self.run_cmd("ip netns exec router iptables -A FORWARD -s 192.168.1.0/24 -d 192.168.3.0/24 -j ACCEPT")
        self.run_cmd("ip netns exec router iptables -A FORWARD -s 192.168.3.0/24 -d 192.168.1.0/24 -j ACCEPT")
        self.run_cmd("ip netns exec router iptables -A FORWARD -s 192.168.1.0/24 -d 192.168.2.0/24 -j ACCEPT")

    def verify_setup(self):
        print("\nVerifying setup")
        result = self.run_cmd("ip netns list", check=False)
        print("Namespaces:", result.stdout)
        result = self.run_cmd("ip link show type bridge", check=False)
        print("Bridges:", result.stdout)
        result = self.run_cmd("ip netns exec router ip addr show", check=False)
        print("Router interfaces:", result.stdout)
        result = self.run_cmd("ip netns exec router iptables -L FORWARD -v -n", check=False)
        print("Firewall rules:", result.stdout)
        for host in self.hosts.keys():
            result = self.run_cmd(f"ip netns exec {host} ip route", check=False)
            print(f"{host} routes:", result.stdout)

    def test_connectivity(self):
        print("\nTesting connectivity")
        result = self.run_cmd("ip netns list", check=False)
        if "hr1" not in result.stdout:
            print("Network not set up")
            return
        
        tests = [("hr1", "192.168.3.10", "fin1", True, "HR1 -> Finance1 (should work)"), 
                 ("fin1", "192.168.1.10", "hr1", True, "Finance1 -> HR1 (should work)"), 
                 ("fin1", "192.168.2.10", "it1", False, "Finance1 -> IT1 (should not work)"), 
                 ("it1", "192.168.1.10", "hr1", True, "IT1 -> HR1 (should work)"), 
                 ("it1", "192.168.3.10", "fin1", True, "IT1 -> Finance1 (should work)"), 
                 ("hr1", "192.168.2.10", "it1", True, "HR1 -> IT1 (should work)"), 
                 ("fin1", "192.168.3.11", "fin2", True, "Finance1 -> Finance2 (should work)")]
        
        for i, (source, dest_ip, dest_name, should_work) in enumerate(tests, 1):
            print(f"\n{i}. Testing {description}:")
            result = self.run_cmd(f"timeout 5 ip netns exec {source} ping -c 2 {dest_ip}", check=False)
            if should_work:
                if result.returncode == 0:
                    print(f"SUCCESS: {source} can reach {dest_name}")
                else:
                    print(f"FAILED: {source} cannot reach {dest_name} (but should be able to)")
            else:
                if result.returncode != 0:
                    print(f"SUCCESS: {source} cannot reach {dest_name} (correctly blocked)")
                else:
                    print(f"FAILED: {source} can reach {dest_name} (should be blocked!)")

    def cleanup(self):
        print("Cleaning up")
        for host in self.hosts.keys():
            self.run_cmd(f"ip netns del {host} 2>/dev/null", check=False)
        self.run_cmd("ip netns del router 2>/dev/null", check=False)
        for bridge in self.bridges:
            self.run_cmd(f"ip link del {bridge} 2>/dev/null", check=False)
        for vlan in [10, 20, 30]:
            self.run_cmd(f"ip link del vr{vlan} 2>/dev/null || true", check=False)
            self.run_cmd(f"ip link del vbr{vlan} 2>/dev/null || true", check=False)
        for host in self.hosts.keys():
            self.run_cmd(f"ip link del v{host} 2>/dev/null || true", check=False)
            self.run_cmd(f"ip link del vb{host} 2>/dev/null || true", check=False)

if __name__ == "__main__":
    manager = NetworkManager()
    action = sys.argv[1]
    if action == "setup":
        manager.setup_network()
    elif action == "test":
        manager.test_connectivity()
    elif action == "cleanup":
        manager.cleanup()
    else:
        print("Invalid action mate")

"""Launch the complete lab, or run the privileged SDN integration smoke test.

Use the system Python with Mininet installed. The controller uses its own venv.
"""

import argparse
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def wait_until(check, description, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {description}")


def run_host(host, command, timeout=8):
    process = host.popen(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError(f"{host.name}: command timed out") from None
    return process.returncode, output.decode("utf-8", errors="replace")


def can_connect(host, python, address, port):
    code = (
        "import socket,sys\n"
        "try:\n"
        f"    socket.create_connection(({address!r}, {port}), timeout=0.5).close()\n"
        "except OSError:\n"
        "    sys.exit(1)\n"
    )
    return run_host(host, [python, "-c", code])[0] == 0


def require_dropped(host, python, port):
    # A refusal is a service failure, not evidence of a switch drop.
    code = (
        "import socket,sys\n"
        "try:\n"
        f"    socket.create_connection(('10.0.0.3', {port}), timeout=0.7).close()\n"
        "except socket.timeout:\n"
        "    sys.exit(0)\n"
        "except OSError as exc:\n"
        "    print(exc); sys.exit(2)\n"
        "sys.exit(1)\n"
    )
    status, output = run_host(host, [python, "-c", code])
    if status != 0:
        raise RuntimeError(f"Expected TCP drop from {host.name} to port {port}: {output}")


def client_command(python, service="database", port=8002):
    return [python, "-m", "client.client", "--host", "10.0.0.2",
            "--service", service, "--port", str(port)]


def write_smoke_policy(path, action, on_sensitive="block"):
    import yaml
    data = {
        "default_action": "deny",
        "inspection": {"on_sensitive": on_sensitive},
        "rules": [
            {"service": "web", "port": 8001, "action": "allow"},
            {"service": "database", "port": 8002, "action": action},
            {"service": "file", "port": 8003, "action": "auto"},
        ],
    }
    temporary = path.with_suffix(".new")
    temporary.write_text(yaml.safe_dump(data), encoding="utf-8")
    temporary.replace(path)


def dump_flows(switch):
    return subprocess.check_output(
        ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch], text=True)


def smoke_test(net, args, policy):
    h1, h2 = net.get("h1", "h2")

    def expect_response(expected, service="database", port=8002):
        status, response = run_host(h1, client_command(args.python, service, port))
        if status != 0 or expected not in response:
            raise RuntimeError(f"Expected {expected!r}; received: {response}")
        return response

    def port_allowed():
        return "tp_dst=8002" in dump_flows(args.switch_name)

    if net.pingAll(timeout="1") != 0:
        raise RuntimeError("Hosts failed the ICMP connectivity check")
    expect_response("Database service:")
    expect_response("File service:", "file", 8003)
    require_dropped(h1, args.python, 8001)
    print("PASS: proxy forwarding, AUTO safe response, and direct-access prevention")

    processes = []
    try:
        for _ in range(6):
            processes.append(h1.popen(
                client_command(args.python, "web", 8001), cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
        for process in processes:
            output, _ = process.communicate(timeout=10)
            if process.returncode or b"Web service:" not in output:
                raise RuntimeError("Concurrent client failed: " + output.decode())
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    print("PASS: six concurrent clients")

    write_smoke_policy(policy, "deny")
    wait_until(lambda: not port_allowed(), "SDN allow-rule removal")
    require_dropped(h2, args.python, 8002)
    expect_response("DENIED:")
    expect_response("Web service:", "web", 8001)
    print("PASS: runtime DENY blocks backend TCP despite learned forwarding")

    write_smoke_policy(policy, "allow")
    wait_until(port_allowed, "SDN allow-rule restoration")
    expect_response("Database service:")
    print("PASS: runtime ALLOW restores backend access without restarts")

    write_smoke_policy(policy, "auto")
    expect_response("BLOCKED: sensitive content detected")
    write_smoke_policy(policy, "auto", on_sensitive="redact")
    response = expect_response("[REDACTED]")
    if "hunter2" in response or "4111 1111 1111 1111" in response:
        raise RuntimeError("Sensitive test values survived redaction")
    policy.write_text("rules: [", encoding="utf-8")
    time.sleep(0.5)  # controller polls every 0.2 seconds in this runner
    expect_response("[REDACTED]")
    print("PASS: AUTO blocks/redacts content; invalid YAML keeps the last good policy")
    print(dump_flows(args.switch_name))
    print("SDN SMOKE TEST PASSED")


def main():
    parser = argparse.ArgumentParser(description="CN Proxy Mininet lab")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "policies.yaml")
    parser.add_argument("--controller-python", default=str(
        ROOT / ".venv-controller" / "bin" / "python"))
    parser.add_argument("--python", default=sys.executable,
                        help="Host Python, with PyYAML installed")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--switch-name", default="s1")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Mininet needs root; run with sudo and the system Python")
    if not 1 <= args.controller_port <= 65535:
        parser.error("--controller-port must be between 1 and 65535")
    if not args.policy.is_file():
        parser.error(f"Policy file not found: {args.policy}")
    # Refuse to take over a controller port or an existing bridge.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", args.controller_port))
    subprocess.run(["ovs-vsctl", "--timeout=3", "show"], check=True,
                   stdout=subprocess.DEVNULL)
    exists = subprocess.run(["ovs-vsctl", "--timeout=3", "br-exists", args.switch_name])
    if exists.returncode == 0:
        parser.error(f"Bridge {args.switch_name} already exists; choose --switch-name")
    if exists.returncode != 2:
        parser.error("Cannot query Open vSwitch")
    subprocess.run([args.controller_python, "-c", "import os_ken, yaml"], check=True)
    subprocess.run([args.python, "-c", "import yaml"], check=True)

    from mininet.cli import CLI
    from mininet.log import setLogLevel
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController
    spec = importlib.util.spec_from_file_location("cn_proxy_topology", ROOT / "mininet" / "topology.py")
    topology = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(topology)
    setLogLevel("info")
    log_dir = Path(tempfile.mkdtemp(prefix="cn-proxy-demo-"))
    policy = args.policy.resolve()
    if args.smoke_test:
        policy = log_dir / "policies.yaml"
        write_smoke_policy(policy, "allow")
    print(f"Logs: {log_dir}\nPolicy: {policy}", flush=True)
    net, processes, logs = None, [], []
    try:
        def launch(name, command, host=None):
            logfile = (log_dir / f"{name}.log").open("w", encoding="utf-8")
            logs.append(logfile)
            popen = subprocess.Popen if host is None else host.popen
            process = popen(command, cwd=str(ROOT), stdout=logfile,
                            stderr=subprocess.STDOUT,
                            env={**os.environ, "PYTHONUNBUFFERED": "1"})
            processes.append(process)
            return process

        controller = launch("controller", [
            args.controller_python, "-m", "controller.run_controller",
            "--policy", str(policy), "--ofp-port", str(args.controller_port),
            "--poll-interval", "0.2",
        ])

        def controller_ready():
            if controller.poll() is not None:
                raise RuntimeError("Controller exited; see controller.log")
            try:
                with socket.create_connection(("127.0.0.1", args.controller_port), timeout=0.3):
                    return True
            except OSError:
                return False

        wait_until(controller_ready, "controller listener")
        net = Mininet(topo=topology.CNProxyTopo(switch_name=args.switch_name),
                      controller=None, switch=OVSSwitch, autoSetMacs=True, build=False)
        net.addController("c0", controller=RemoteController,
                          ip="127.0.0.1", port=args.controller_port)
        net.build()
        net.start()
        if not net.waitConnected(timeout=10):
            raise RuntimeError("Switch failed to connect to the controller")
        h1, h2, h3 = net.get("h1", "h2", "h3")
        launch("server", [args.python, "-m", "server.server"], h3)
        launch("proxy", [args.python, "-m", "proxy.proxy", "--server-host",
                          "10.0.0.3", "--policy", str(policy)], h2)
        wait_until(lambda: can_connect(h1, args.python, "10.0.0.2", 9000),
                   "proxy listener")
        # Check services locally so startup also works with every policy DENY.
        for port in (8001, 8002, 8003):
            wait_until(lambda port=port: can_connect(h3, args.python, "127.0.0.1", port),
                       f"service on port {port}")
        if args.smoke_test:
            smoke_test(net, args, policy)
        else:
            print("Client: h1 python3 -m client.client --host 10.0.0.2 --service web --port 8001")
            print(f"Edit {policy} in another terminal; the controller polls every 0.2s.")
            print(f"Flows: sh ovs-ofctl -O OpenFlow13 dump-flows {args.switch_name}")
            CLI(net)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Lab failed: {exc}\nInspect logs in {log_dir}", file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if net is not None:
            net.stop()
        for logfile in logs:
            logfile.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Run with: python -m controller.run_controller --help."""

import argparse
import ipaddress
import logging
import math
import os
from pathlib import Path
import signal
import socket


def main():
    parser = argparse.ArgumentParser(description="CN Proxy OS-Ken controller")
    parser.add_argument("--policy", type=Path, default=(
        Path(__file__).resolve().parents[1] / "config" / "policies.yaml"))
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--ofp-port", type=int, default=6653)
    parser.add_argument("--proxy-ip", default="10.0.0.2")
    parser.add_argument("--server-ip", default="10.0.0.3")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args()
    if not 1 <= args.ofp_port <= 65535:
        parser.error("--ofp-port must be between 1 and 65535")
    if not math.isfinite(args.poll_interval) or args.poll_interval <= 0:
        parser.error("--poll-interval must be positive and finite")
    try:
        ipaddress.IPv4Address(args.proxy_ip)
        ipaddress.IPv4Address(args.server_ip)
    except ipaddress.AddressValueError as exc:
        parser.error(str(exc))
    if not args.policy.is_file():
        parser.error(f"Policy file not found: {args.policy}")
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((args.listen_host, args.ofp_port))
    except OSError as exc:
        parser.error(f"Cannot listen on {args.listen_host}:{args.ofp_port}: {exc}")

    os.environ["CN_PROXY_POLICY"] = str(args.policy.resolve())
    os.environ["CN_SDN_PROXY_IP"] = args.proxy_ip
    os.environ["CN_SDN_SERVER_IP"] = args.server_ip
    os.environ["CN_SDN_POLL_INTERVAL"] = str(args.poll_interval)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    from os_ken import cfg
    from os_ken.base import app_manager
    from os_ken.lib import hub

    if hub.HUB_TYPE == "native":
        # OS-Ken 4.2.2 run_apps() calls .kill() on native threads during
        # KeyboardInterrupt cleanup. SIGINT's OS default stops all threads
        # and closes sockets without that incompatible green-thread path.
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    cfg.CONF(args=["--ofp-listen-host", args.listen_host,
                   "--ofp-tcp-listen-port", str(args.ofp_port)],
             project="cn-proxy")
    try:
        app_manager.AppManager.run_apps(["controller.simple_switch"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

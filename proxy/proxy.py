import argparse
import socket
import threading

try:
    from parser import parse_request
    from policy import PolicyEngine
    from logger import get_logger

except ImportError:
    from .parser import parse_request
    from .policy import PolicyEngine
    from .logger import get_logger


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PROXY_PORT = 9000
DEFAULT_SERVER_HOST = "10.0.0.3"

BUFFER_SIZE = 4096
BACKLOG = 100

logger = get_logger()


def forward_to_server(
    server_host,
    server_port,
    request
):

    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    ) as server_sock:

        server_sock.settimeout(5)

        server_sock.connect(
            (server_host, server_port)
        )

        server_sock.sendall(
            request.encode("utf-8")
        )

        chunks = []

        while True:

            chunk = server_sock.recv(
                BUFFER_SIZE
            )

            if not chunk:
                break

            chunks.append(chunk)

    return b"".join(chunks).decode(
        "utf-8",
        errors="replace"
    )


def handle_client(
    conn,
    address,
    policy_engine,
    server_host
):

    client_ip, client_port = address

    with conn:

        try:

            data = conn.recv(
                BUFFER_SIZE
            )

            if not data:
                return

            raw_request = data.decode(
                "utf-8",
                errors="replace"
            ).strip()

            try:

                request = parse_request(
                    raw_request
                )

            except ValueError as exc:

                logger.warning(
                    "INVALID | client=%s:%s "
                    "| request=%r | error=%s",
                    client_ip,
                    client_port,
                    raw_request,
                    exc
                )

                conn.sendall(
                    f"ERROR: {exc}\n".encode(
                        "utf-8"
                    )
                )

                return

            service = request["service"]
            service_port = request["port"]

            action = policy_engine.evaluate(
                service,
                service_port
            )

            logger.info(
                "%s | client=%s:%s "
                "| service=%s | port=%s",
                action,
                client_ip,
                client_port,
                service,
                service_port
            )

            if action == "DENY":

                response = (
                    f"DENIED: policy blocks "
                    f"{service} on port "
                    f"{service_port}\n"
                )

                conn.sendall(
                    response.encode("utf-8")
                )

                return

            try:

                response = forward_to_server(
                    server_host,
                    service_port,
                    raw_request
                )

                conn.sendall(
                    response.encode("utf-8")
                )

            except (
                ConnectionError,
                OSError,
                socket.timeout
            ) as exc:

                logger.error(
                    "SERVER_ERROR | service=%s "
                    "| port=%s | error=%s",
                    service,
                    service_port,
                    exc
                )

                conn.sendall(
                    b"ERROR: simulated "
                    b"service unavailable\n"
                )

        except (
            ConnectionError,
            OSError
        ) as exc:

            logger.error(
                "CLIENT_ERROR | client=%s:%s "
                "| error=%s",
                client_ip,
                client_port,
                exc
            )


def run_proxy(
    host,
    proxy_port,
    server_host,
    policy_path=None
):

    policy_engine = PolicyEngine(
        policy_path
    )

    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    ) as proxy_sock:

        proxy_sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        proxy_sock.bind(
            (host, proxy_port)
        )

        proxy_sock.listen(BACKLOG)

        logger.info(
            "TCP proxy listening on "
            "%s:%s; server=%s",
            host,
            proxy_port,
            server_host
        )

        while True:

            conn, address = (
                proxy_sock.accept()
            )

            thread = threading.Thread(
                target=handle_client,
                args=(
                    conn,
                    address,
                    policy_engine,
                    server_host
                ),
                daemon=True
            )

            thread.start()


def main():

    parser = argparse.ArgumentParser(
        description="CN Proxy"
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST
    )

    parser.add_argument(
        "--proxy-port",
        type=int,
        default=DEFAULT_PROXY_PORT
    )

    parser.add_argument(
        "--server-host",
        default=DEFAULT_SERVER_HOST
    )

    parser.add_argument(
        "--policy",
        default=None
    )

    args = parser.parse_args()

    try:

        run_proxy(
            args.host,
            args.proxy_port,
            args.server_host,
            args.policy
        )

    except KeyboardInterrupt:

        logger.info(
            "Proxy stopped."
        )


if __name__ == "__main__":
    main()
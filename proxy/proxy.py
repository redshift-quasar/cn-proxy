import argparse
import socket
import threading

try:
    from parser import parse_request, MAX_REQUEST_BYTES
    from policy import PolicyEngine
    from logger import get_logger
    from classifier import inspect as inspect_content

except ImportError:
    from .parser import parse_request, MAX_REQUEST_BYTES
    from .policy import PolicyEngine
    from .logger import get_logger
    from .classifier import inspect as inspect_content


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PROXY_PORT = 9000
DEFAULT_SERVER_HOST = "10.0.0.3"

BUFFER_SIZE = 4096
BACKLOG = 100
CLIENT_TIMEOUT = 5      # seconds to wait for a client request
SERVER_TIMEOUT = 5      # seconds to wait for the backend service
MAX_RESPONSE_BYTES = 1024 * 1024   # refuse absurdly large backend replies

logger = get_logger()


def recv_request(conn):
    """Read one request: until newline, EOF, or MAX_REQUEST_BYTES.

    TCP is a byte stream, so a single recv() may return a partial request.
    Returns raw bytes (possibly empty if the client sent nothing).
    """

    data = b""

    while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:

        chunk = conn.recv(BUFFER_SIZE)

        if not chunk:
            break

        data += chunk

    return data


def forward_to_server(server_host, server_port, request):

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:

        server_sock.settimeout(SERVER_TIMEOUT)
        server_sock.connect((server_host, server_port))
        server_sock.sendall(request.encode("utf-8"))

        chunks = []
        received = 0

        while True:

            chunk = server_sock.recv(BUFFER_SIZE)

            if not chunk:
                break

            chunks.append(chunk)
            received += len(chunk)

            if received > MAX_RESPONSE_BYTES:
                raise OSError("service response too large")

    return b"".join(chunks).decode("utf-8", errors="replace")


def handle_client(conn, address, policy_engine, server_host):

    client_ip, client_port = address

    with conn:

        conn.settimeout(CLIENT_TIMEOUT)

        try:

            data = recv_request(conn)

            if not data:
                return

            raw_request = data.decode("utf-8", errors="replace").strip()

            try:
                request = parse_request(raw_request)

            except ValueError as exc:

                logger.warning(
                    "INVALID | client=%s:%s | request=%r | error=%s",
                    client_ip, client_port, raw_request[:200], exc,
                )

                conn.sendall(f"ERROR: {exc}\n".encode("utf-8"))
                return

            service = request["service"]
            service_port = request["port"]

            action = policy_engine.evaluate(service, service_port)

            logger.info(
                "%s | client=%s:%s | service=%s | port=%s",
                action, client_ip, client_port, service, service_port,
            )

            if action == "DENY":

                conn.sendall(
                    (
                        f"DENIED: policy blocks {service} "
                        f"on port {service_port}\n"
                    ).encode("utf-8")
                )
                return

            try:
                # Newline-terminated, same framing the client uses.
                response = forward_to_server(
                    server_host, service_port, raw_request + "\n"
                )

            except (ConnectionError, OSError) as exc:  # includes timeout

                logger.error(
                    "SERVER_ERROR | service=%s | port=%s | error=%s",
                    service, service_port, exc,
                )

                conn.sendall(b"ERROR: simulated service unavailable\n")
                return

            if action == "AUTO":
                # Dynamic decision: look at what the service actually returned.
                verdict = inspect_content(
                    response, policy_engine.on_sensitive()
                )

                if verdict.action == "BLOCK":

                    logger.warning(
                        "AUTO_BLOCK | client=%s:%s | service=%s | port=%s "
                        "| found=%s",
                        client_ip, client_port, service, service_port,
                        ",".join(verdict.labels),
                    )

                    conn.sendall(
                        (
                            f"BLOCKED: sensitive content detected in "
                            f"{service} response "
                            f"({', '.join(verdict.labels)})\n"
                        ).encode("utf-8")
                    )
                    return

                if verdict.action == "REDACT":

                    logger.warning(
                        "AUTO_REDACT | client=%s:%s | service=%s | port=%s "
                        "| found=%s",
                        client_ip, client_port, service, service_port,
                        ",".join(verdict.labels),
                    )

                response = verdict.text

            conn.sendall(response.encode("utf-8"))

        except socket.timeout:

            logger.warning(
                "CLIENT_TIMEOUT | client=%s:%s", client_ip, client_port
            )

        except (ConnectionError, OSError) as exc:

            logger.error(
                "CLIENT_ERROR | client=%s:%s | error=%s",
                client_ip, client_port, exc,
            )

        except Exception:  # never let one bad connection kill the thread silently

            logger.exception(
                "INTERNAL_ERROR | client=%s:%s", client_ip, client_port
            )

            try:
                conn.sendall(b"ERROR: internal proxy error\n")
            except OSError:
                pass


def run_proxy(host, proxy_port, server_host, policy_path=None,
              policy_engine=None):
    """Run the proxy. Pass a prebuilt policy_engine to attach listeners
    (e.g. the SDN integration layer) before the proxy starts."""

    if policy_engine is None:
        policy_engine = PolicyEngine(policy_path, logger=logger)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as proxy_sock:

        proxy_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        proxy_sock.bind((host, proxy_port))
        proxy_sock.listen(BACKLOG)

        logger.info(
            "TCP proxy listening on %s:%s; server=%s",
            host, proxy_port, server_host,
        )

        while True:

            conn, address = proxy_sock.accept()

            threading.Thread(
                target=handle_client,
                args=(conn, address, policy_engine, server_host),
                daemon=True,
            ).start()


def main():

    arg_parser = argparse.ArgumentParser(description="CN Proxy")

    arg_parser.add_argument("--host", default=DEFAULT_HOST)
    arg_parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    arg_parser.add_argument("--server-host", default=DEFAULT_SERVER_HOST)
    arg_parser.add_argument("--policy", default=None)

    args = arg_parser.parse_args()

    try:
        run_proxy(args.host, args.proxy_port, args.server_host, args.policy)

    except KeyboardInterrupt:
        logger.info("Proxy stopped.")


if __name__ == "__main__":
    main()

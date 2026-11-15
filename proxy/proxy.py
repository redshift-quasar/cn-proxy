import argparse
import socket
import signal
import threading
import time

import yaml

try:
    from .protection import Protection
    from .http_proxy import handle_http, reply
except ImportError:
    from protection import Protection
    from http_proxy import handle_http, reply

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


def recv_request(conn, timeout=CLIENT_TIMEOUT):
    """Read one request: until newline, EOF, or MAX_REQUEST_BYTES.

    TCP is a byte stream, so a single recv() may return a partial request.
    Returns raw bytes (possibly empty if the client sent nothing).
    """

    data = b""
    deadline = time.monotonic() + timeout

    while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        conn.settimeout(remaining)
        chunk = conn.recv(min(BUFFER_SIZE, MAX_REQUEST_BYTES + 1 - len(data)))

        if not chunk:
            break

        data += chunk

    if ((len(data) >= MAX_REQUEST_BYTES and b"\n" not in data)
            or len(data) > MAX_REQUEST_BYTES):
        raise ValueError("Request too large")
    if b"\n" in data and data.split(b"\n", 1)[1].strip():
        raise ValueError("Only one request per connection is supported")
    return data


def forward_to_server(server_host, server_port, request, timeout=SERVER_TIMEOUT):

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:

        deadline = time.monotonic() + timeout
        server_sock.settimeout(timeout)
        server_sock.connect((server_host, server_port))
        server_sock.sendall(request.encode("utf-8"))

        chunks = []
        received = 0

        while True:

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            server_sock.settimeout(remaining)
            chunk = server_sock.recv(BUFFER_SIZE)

            if not chunk:
                break

            chunks.append(chunk)
            received += len(chunk)

            if received > MAX_RESPONSE_BYTES:
                raise OSError("service response too large")

    return b"".join(chunks).decode("utf-8", errors="replace")


def handle_client(conn, address, policy_engine, server_host, protection=None):

    client_ip, client_port = address

    with conn:

        conn.settimeout(CLIENT_TIMEOUT)

        try:

            data = recv_request(conn, protection.settings()["request_timeout_seconds"]
                                if protection else CLIENT_TIMEOUT)

            if not data:
                return

            raw_request = data.decode("utf-8", errors="replace").strip()

            try:
                request = parse_request(raw_request)

            except ValueError as exc:

                if protection:
                    protection.violation(client_ip)

                logger.warning(
                    "INVALID | client=%s:%s | error=%s",
                    client_ip, client_port, exc,
                )

                conn.sendall(f"ERROR: {exc}\n".encode("utf-8"))
                return

            service = request["service"]
            service_port = request["port"]

            rules, settings = policy_engine.snapshot()
            action = rules.get((service, service_port), settings["default_action"])

            logger.info(
                "%s | client=%s:%s | service=%s | port=%s",
                action, client_ip, client_port, service, service_port,
            )

            # Reading the request changes the socket timeout to the remaining
            # deadline. Give the response write its own bounded timeout.
            conn.settimeout(settings["protection"]["request_timeout_seconds"])
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
                    server_host, service_port, raw_request + "\n",
                    settings["protection"]["request_timeout_seconds"]
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
                    response, settings["on_sensitive"]
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

        except ValueError as exc:
            if protection:
                protection.violation(client_ip)
            try:
                conn.sendall(f"ERROR: {exc}\n".encode())
            except OSError:
                pass

        except socket.timeout:

            if protection:
                protection.violation(client_ip)

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

    protection = Protection(policy_engine)
    serve_listener(host, proxy_port, policy_engine, server_host, protection)


def serve_listener(host, port, policy, server_host, protection, http=False,
                   ready=None, stop=None):
    """Admit before creating a worker, bounding active threads across listeners."""
    workers = {}
    worker_lock = threading.Lock()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(BACKLOG)
        listener.settimeout(0.2)
        logger.info("%s proxy listening on %s:%s", "HTTP" if http else "TCP", host, listener.getsockname()[1])
        if ready:
            ready(listener.getsockname()[1])
        try:
            while stop is None or not stop.is_set():
                try:
                    conn, address = listener.accept()
                except socket.timeout:
                    continue
                reason = protection.admit(address[0])
                if reason:
                    logger.warning("CLIENT_REJECT | client=%s | reason=%s", address[0], reason)
                    with conn:
                        conn.settimeout(0.1)
                        try:
                            if http:
                                reply(conn, 429, reason, headers=[('Retry-After', '1')])
                            else:
                                conn.sendall(f"REJECTED: {reason}\n".encode())
                        except OSError:
                            pass
                    continue

                def worker(connection=conn, peer=address):
                    try:
                        if http:
                            handle_http(connection, peer, policy, server_host, protection, logger)
                        else:
                            handle_client(connection, peer, policy, server_host, protection)
                    except Exception:
                        logger.exception("WORKER_ERROR | client=%s", peer[0])
                    finally:
                        connection.close()
                        protection.release(peer[0])
                        with worker_lock:
                            workers.pop(connection, None)
                try:
                    thread = threading.Thread(target=worker)
                    with worker_lock:
                        workers[conn] = thread
                    thread.start()
                except Exception:
                    with worker_lock:
                        workers.pop(conn, None)
                    conn.close()
                    protection.release(address[0])
                    raise
        finally:
            # Wake incomplete client reads, then wait for bounded backend work.
            with worker_lock:
                pending = list(workers.items())
            for connection, _ in pending:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            for _, thread in pending:
                thread.join()


def main():

    arg_parser = argparse.ArgumentParser(description="CN Proxy")

    arg_parser.add_argument("--host", default=DEFAULT_HOST)
    arg_parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    arg_parser.add_argument("--server-host", default=DEFAULT_SERVER_HOST)
    arg_parser.add_argument("--policy", default=None)
    arg_parser.add_argument("--http-port", type=int, default=0,
                            help="Enable restricted HTTP GET/HEAD forward proxy on this port")

    args = arg_parser.parse_args()

    if not 0 <= args.http_port <= 65535 or not 1 <= args.proxy_port <= 65535:
        arg_parser.error("Invalid listener port")

    try:
        if args.http_port == args.proxy_port:
            arg_parser.error("HTTP and legacy TCP listeners need distinct ports")
        policy = PolicyEngine(args.policy, logger=logger)
        protection = Protection(policy)
        stop = threading.Event()
        threads, errors = [], []
        previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            for port, is_http in ((args.proxy_port, False), (args.http_port, True)):
                if not port:
                    continue
                startup = threading.Event()

                def listen(port=port, is_http=is_http, startup=startup):
                    try:
                        serve_listener(args.host, port, policy, args.server_host,
                                       protection, http=is_http,
                                       ready=lambda _: startup.set(), stop=stop)
                    except Exception as exc:
                        errors.append(exc)
                        stop.set()
                    finally:
                        startup.set()

                thread = threading.Thread(target=listen)
                threads.append(thread)
                thread.start()
                startup.wait()
                if errors:
                    raise errors[0]
            while not stop.wait(0.2):
                pass
            if errors:
                raise errors[0]
        finally:
            stop.set()
            for thread in threads:
                thread.join()
            signal.signal(signal.SIGTERM, previous)

    except KeyboardInterrupt:
        logger.info("Proxy stopped.")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        arg_parser.exit(1, f"Proxy failed: {exc}\n")


if __name__ == "__main__":
    main()

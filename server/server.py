import os
import socket
import threading
import time

try:
    from services import SERVICES, response_for_port
except ImportError:
    from .services import SERVICES, response_for_port


HOST = "0.0.0.0"
BUFFER_SIZE = 4096
MAX_REQUEST_BYTES = 1024
CLIENT_TIMEOUT = 5
VERBOSE = os.environ.get("CN_SERVER_QUIET") != "1"   # set to 1 for benchmarks


def recv_request(conn):
    """Read until newline/EOF so fragmented requests are handled."""

    data = b""
    deadline = time.monotonic() + CLIENT_TIMEOUT
    while True:
        is_http = data.startswith((b"GET ", b"HEAD "))
        limit = 16384 if is_http else MAX_REQUEST_BYTES
        if (b"\r\n\r\n" in data if is_http else b"\n" in data):
            return data
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout()
        if len(data) >= limit:
            raise ValueError("Request too large")
        conn.settimeout(remaining)
        chunk = conn.recv(min(BUFFER_SIZE, limit - len(data)))
        if not chunk:
            return data
        data += chunk


def handle_client(conn, address, port):

    with conn:

        conn.settimeout(CLIENT_TIMEOUT)

        try:

            data = recv_request(conn)

            if not data:
                return

            request = data.decode("utf-8", errors="replace").strip()

            if request.startswith(("GET ", "HEAD ")):
                # Minimal HTTP demo backend alongside the existing TCP protocol.
                if b"\r\n\r\n" not in data:
                    return
                body = response_for_port(port).encode("utf-8")
                headers = ("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
                conn.sendall(headers.encode("ascii") + (b"" if request.startswith("HEAD ") else body))
                return

            response = f"{response_for_port(port)}\nReceived: {request}"

            conn.sendall(response.encode("utf-8"))

            if VERBOSE:
                print(
                    f"[{address[0]}:{address[1]}] "
                    f"port={port} request={request}"
                )

        except (OSError, ValueError) as exc:   # includes socket.timeout

            print(f"Connection error from {address}: {exc}")


def serve_port(port, service_name):

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:

        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, port))
        server_sock.listen(100)

        print(f"{service_name} service listening on {HOST}:{port}")

        capacity = threading.BoundedSemaphore(100)
        def worker(conn, address):
            try:
                handle_client(conn, address, port)
            finally:
                capacity.release()

        while True:

            conn, address = server_sock.accept()

            if not capacity.acquire(blocking=False):
                conn.close()
                continue
            try:
                threading.Thread(target=worker, args=(conn, address), daemon=True).start()
            except Exception:
                capacity.release()
                conn.close()
                raise


def main():

    threads = []

    for name, info in SERVICES.items():

        thread = threading.Thread(
            target=serve_port,
            args=(info["port"], name),
            daemon=True,
        )

        thread.start()
        threads.append(thread)

    print("All simulated services are running.")

    try:
        for thread in threads:
            thread.join()

    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()

import os
import socket
import threading

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

    while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:

        chunk = conn.recv(BUFFER_SIZE)

        if not chunk:
            break

        data += chunk

    return data


def handle_client(conn, address, port):

    with conn:

        conn.settimeout(CLIENT_TIMEOUT)

        try:

            data = recv_request(conn)

            if not data:
                return

            request = data.decode("utf-8", errors="replace").strip()

            response = f"{response_for_port(port)}\nReceived: {request}"

            conn.sendall(response.encode("utf-8"))

            if VERBOSE:
                print(
                    f"[{address[0]}:{address[1]}] "
                    f"port={port} request={request}"
                )

        except OSError as exc:   # includes socket.timeout

            print(f"Connection error from {address}: {exc}")


def serve_port(port, service_name):

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:

        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((HOST, port))
        server_sock.listen(100)

        print(f"{service_name} service listening on {HOST}:{port}")

        while True:

            conn, address = server_sock.accept()

            threading.Thread(
                target=handle_client,
                args=(conn, address, port),
                daemon=True,
            ).start()


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

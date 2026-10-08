import socket
import threading

try:
    from services import SERVICES, response_for_port
except ImportError:
    from .services import SERVICES, response_for_port


HOST = "0.0.0.0"
BUFFER_SIZE = 4096


def handle_client(conn, address, port):

    with conn:

        try:

            data = conn.recv(BUFFER_SIZE)

            if not data:
                return

            request = data.decode(
                "utf-8",
                errors="replace"
            ).strip()

            response = (
                f"{response_for_port(port)}\n"
                f"Received: {request}"
            )

            conn.sendall(
                response.encode("utf-8")
            )

            print(
                f"[{address[0]}:{address[1]}] "
                f"port={port} request={request}"
            )

        except OSError as exc:

            print(
                f"Connection error from "
                f"{address}: {exc}"
            )


def serve_port(port):

    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    ) as server_sock:

        server_sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server_sock.bind(
            (HOST, port)
        )

        server_sock.listen(50)

        service_name = next(
            name
            for name, info in SERVICES.items()
            if info["port"] == port
        )

        print(
            f"{service_name} service listening "
            f"on {HOST}:{port}"
        )

        while True:

            conn, address = (
                server_sock.accept()
            )

            thread = threading.Thread(
                target=handle_client,
                args=(
                    conn,
                    address,
                    port
                ),
                daemon=True
            )

            thread.start()


def main():

    threads = []

    for service in SERVICES.values():

        thread = threading.Thread(
            target=serve_port,
            args=(service["port"],),
            daemon=True
        )

        thread.start()

        threads.append(thread)

    print(
        "All simulated services are running."
    )

    try:

        for thread in threads:
            thread.join()

    except KeyboardInterrupt:

        print("\nServer stopped.")


if __name__ == "__main__":
    main()
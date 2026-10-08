import argparse
import socket


DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 9000
BUFFER_SIZE = 4096


def send_request(proxy_host, proxy_port, service, service_port):
    request = f"service={service};port={service_port}\n"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect((proxy_host, proxy_port))
        sock.sendall(request.encode("utf-8"))

        response = sock.recv(BUFFER_SIZE).decode(
            "utf-8", errors="replace"
        )

    print(f"Request : {request.strip()}")
    print(f"Response: {response.strip()}")


def main():
    parser = argparse.ArgumentParser(description="CN Proxy TCP client")

    parser.add_argument("--host", default=DEFAULT_PROXY_HOST)
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument("--service", required=True)
    parser.add_argument("--port", type=int, required=True)

    args = parser.parse_args()

    send_request(
        args.host,
        args.proxy_port,
        args.service,
        args.port,
    )


if __name__ == "__main__":
    main()
import argparse
import socket
import sys

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 9000
BUFFER_SIZE = 4096
TIMEOUT = 10


def send_request(proxy_host, proxy_port, service, service_port,
                 timeout=TIMEOUT):
    """Send one request and return (request, response) text.

    Raises OSError on connection failure/timeout.
    For the baseline (direct to server) use host=<server ip>,
    proxy_port=<service port>.
    """

    request = f"service={service};port={service_port}\n"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:

        sock.settimeout(timeout)
        sock.connect((proxy_host, proxy_port))
        sock.sendall(request.encode("utf-8"))

        chunks = []

        while True:   # read until the peer closes the connection

            chunk = sock.recv(BUFFER_SIZE)

            if not chunk:
                break

            chunks.append(chunk)

    return request.strip(), b"".join(chunks).decode("utf-8", errors="replace")


def main():

    arg_parser = argparse.ArgumentParser(description="CN Proxy TCP client")

    arg_parser.add_argument("--host", default=DEFAULT_PROXY_HOST)
    arg_parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    arg_parser.add_argument("--service", required=True)
    arg_parser.add_argument("--port", type=int, required=True)

    args = arg_parser.parse_args()

    try:
        request, response = send_request(
            args.host, args.proxy_port, args.service, args.port
        )

    except OSError as exc:
        print(f"Connection failed: {exc}")
        sys.exit(1)

    print(f"Request : {request}")
    print(f"Response: {response.strip()}")


if __name__ == "__main__":
    main()

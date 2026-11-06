import argparse
import csv
import os
import socket
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = "/home/bhamini/cn-proxy"
DEFAULT_OUTPUT = os.path.join(ROOT, "experiments/results/benchmark.csv")


def send_one(request_id, host, proxy_port, service, service_port,
             concurrency, timeout):
    request = f"service={service};port={service_port}\n".encode()
    start = time.perf_counter()
    response = b""
    error = ""

    try:
        with socket.create_connection(
            (host, proxy_port), timeout=timeout
        ) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            chunks = []

            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)

            response = b"".join(chunks)

        text = response.decode("utf-8", errors="replace")
        success = text.startswith("Web service:") and not text.startswith(
            ("DENIED:", "BLOCKED:", "ERROR:")
        )
        if not success:
            error = text.strip()[:200] or "Unexpected response"

    except (OSError, TimeoutError) as exc:
        success = False
        error = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "request_id": request_id,
        "service": service,
        "concurrency": concurrency,
        "latency_ms": round(latency_ms, 3),
        "success": success,
        "response_bytes": len(response),
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser(
        description="CN Proxy latency, throughput and concurrency benchmark"
    )
    parser.add_argument("--host", default="10.0.0.2")
    parser.add_argument("--proxy-port", type=int, default=9000)
    parser.add_argument("--service", default="web")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")

    print(f"Target: {args.host}:{args.proxy_port}")
    print(f"Service: {args.service}, backend port: {args.port}")
    print(f"Requests: {args.requests}, concurrency: {args.concurrency}")

    start = time.perf_counter()
    results = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                send_one, i, args.host, args.proxy_port,
                args.service, args.port, args.concurrency, args.timeout
            )
            for i in range(1, args.requests + 1)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    elapsed = time.perf_counter() - start
    results.sort(key=lambda row: row["request_id"])

    latencies = [row["latency_ms"] for row in results]
    successes = sum(row["success"] for row in results)
    failures = len(results) - successes
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(0.95 * len(ordered)) - 1)]

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    columns = [
        "request_id", "service", "concurrency", "latency_ms",
        "success", "response_bytes", "error"
    ]

    with open(args.output, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)

    print("\n=== CN PROXY BENCHMARK RESULTS ===")
    print(f"Completed requests : {len(results)}")
    print(f"Successful         : {successes}")
    print(f"Failed             : {failures}")
    print(f"Elapsed time       : {elapsed:.3f} seconds")
    print(f"Throughput         : {len(results) / elapsed:.2f} requests/sec")
    print(f"Average latency    : {statistics.mean(latencies):.3f} ms")
    print(f"Median latency     : {statistics.median(latencies):.3f} ms")
    print(f"P95 latency        : {p95:.3f} ms")
    print(f"CSV saved to       : {args.output}")


if __name__ == "__main__":
    main()

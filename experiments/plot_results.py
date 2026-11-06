import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from pathlib import Path

output = Path("evidence/performance")
output.mkdir(parents=True, exist_ok=True)

concurrency = [1, 5, 10, 20]

# Direct communication measurements
direct_throughput = [1518.95, 4374.03, 6547.83, 13269.83]
direct_latency = [0.628, 0.968, 1.271, 1.372]

# Proxy measurements; concurrency 20 is the mean of three runs
proxy_throughput = [829.41, 2226.78, 3085.82, 5159.71]
proxy_latency = [1.166, 2.056, 3.007, 3.950]

# Throughput comparison
plt.figure(figsize=(9, 5))
plt.plot(concurrency, direct_throughput, marker="o",
         label="Direct communication")
plt.plot(concurrency, proxy_throughput, marker="o",
         label="Proxy communication")

plt.title("Throughput: Direct vs Proxy")
plt.xlabel("Concurrency")
plt.ylabel("Throughput (requests/second)")
plt.xticks(concurrency)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(output / "throughput_comparison.png", dpi=200)
plt.close()

# Average latency comparison
plt.figure(figsize=(9, 5))
plt.plot(concurrency, direct_latency, marker="o",
         label="Direct communication")
plt.plot(concurrency, proxy_latency, marker="o",
         label="Proxy communication")

plt.title("Average Latency: Direct vs Proxy")
plt.xlabel("Concurrency")
plt.ylabel("Average latency (ms)")
plt.xticks(concurrency)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(output / "latency_comparison.png", dpi=200)
plt.close()

print("Both performance graphs generated successfully.")

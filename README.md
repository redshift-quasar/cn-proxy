# CN Proxy

A college Computer Networks project combining a Python application proxy with
OS-Ken and OpenFlow 1.3 enforcement in Mininet/Open vSwitch.

**[DEMO.md](DEMO.md) is the authoritative setup, demonstration, troubleshooting,
and viva guide.** It includes the five-host topology, copy-paste commands,
policy settings, negative tests, expected results, and verification boundaries.

The original newline-framed TCP listener on port 9000 remains supported. An
optional restricted HTTP forward proxy supports GET/HEAD on port 8080. Both
share per-source request/concurrency limits and temporary blocking. YAML rules
select ALLOW, DENY, or AUTO response inspection (block/redact). The SDN policy
table prevents direct client-to-backend IPv4 TCP access before MAC learning.
Database and file services are simulated text responders, not SQL/FTP servers.

## Quick launch

From the repository root on a prepared Linux/Mininet system:

```bash
sudo /usr/bin/python3 mininet/run_demo.py --extra-clients 2 --http-port 8080
```

This uses `config/policies.yaml`: all three services currently use AUTO, so the
fake sensitive database response is blocked. Follow DEMO.md to use an isolated
presentation policy and avoid changing the working configuration.

## Local verification

```bash
.venv-controller/bin/python -m unittest discover -s tests -v
```

The controller venv needs `requirements.txt`; system Python needs Mininet and
PyYAML. The nonprivileged tests do not establish real OVS forwarding. See
[verification results](docs/test_results.md) and DEMO.md for the privileged smoke
command and remaining live checks.

## Source map

| Path | Role |
| --- | --- |
| client/client.py | Original custom TCP client |
| proxy/proxy.py | Listeners, bounded workers, custom forwarding |
| proxy/http_proxy.py | Restricted HTTP parser/forwarder |
| proxy/protection.py | Shared source-IP admission and bans |
| proxy/policy.py | Validated YAML snapshots and hot reload |
| proxy/classifier.py | Pattern/checksum sensitivity detection |
| server/ | Three simulated services with fake test data |
| controller/ | OS-Ken events and two-table flow management |
| mininet/ | Topology and isolated-process lab runner |
| config/policies.yaml | Working policy |
| tests/ | Socket, policy, flow and controller-wire regression tests |
| experiments/ | Benchmark tools and historical measurements |
| evidence/ | Preserved screenshots, logs, and performance figures |

Historical [performance evaluation](docs/performance_evaluation.md),
[member integration report](docs/member3_evaluation.md), and evidence are retained.
Measurements predate the shared admission limits; repeat benchmarks under an
explicit benchmark policy in a separate local baseline network. They are not
current throughput guarantees. Plot generation additionally requires matplotlib.

The teaching proxy has explicit HTTP/TLS, identity, inspection and availability
limitations detailed in DEMO.md. Licensed under [MIT](LICENSE).

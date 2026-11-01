# Member 3 – Integration, Testing and Evaluation

## 1. Role and Responsibilities

Member 3 is responsible for integrating the application-level proxy and Software-Defined Networking (SDN) components, testing the complete system, evaluating its performance, and documenting the results.

The main responsibilities include:

- Integrating the TCP proxy with the backend services and SDN environment.
- Verifying communication between clients, proxy, backend server, and SDN switch.
- Testing application-level traffic filtering and runtime policy changes.
- Testing concurrent client connections.
- Evaluating system behaviour under different conditions.
- Measuring latency, throughput, and request success rate.
- Comparing direct client-to-server communication with communication through the proxy.
- Recording test results and preparing project evaluation documentation.

## 2. System Integration

The integrated system uses three Mininet hosts:

| Host | IP Address | Role |
|---|---|---|
| h1 | 10.0.0.1 | Client |
| h2 | 10.0.0.2 | TCP Proxy |
| h3 | 10.0.0.3 | Backend Server |

The client sends requests to the proxy running on h2. The proxy evaluates the request according to the configured traffic policies and forwards permitted requests to the appropriate backend service on h3.

The backend provides three services:

- Web service – Port 8001
- Database service – Port 8002
- File service – Port 8003

The network topology uses an Open vSwitch switch configured for OpenFlow 1.3.

## 3. Integration Verification

The following checks were performed:

- Started the integrated Mininet topology.
- Verified connectivity between hosts using `pingall`.
- Confirmed successful communication between the client and proxy.
- Verified that a web request reached the backend through the proxy.
- Inspected OpenFlow rules to verify the configured network traffic handling.

The `pingall` test completed with 6 out of 6 successful connectivity checks and 0% packet loss.

## 4. Traffic Policy Testing

Application-level traffic filtering was tested by changing the database service policy while the integrated demo was running.

The following policy behaviours were verified:

- **Allow:** The request was permitted.
- **Deny:** The request was explicitly denied by the proxy.
- **Auto:** The proxy applied its automatic policy evaluation, including blocking the tested sensitive database response.

These tests demonstrated that policy changes could be applied without restarting the integrated demo.

## 5. Automated Testing

The following automated test suites were executed:

| Test Suite | Result |
|---|---|
| Proxy integration tests | Passed |
| Controller wire tests | 3 tests passed |
| SDN tests | 16 tests passed |
| Combined automated test suite | 22 tests passed |

An SDN smoke test was also executed successfully. Its output was saved in `experiments/results/smoke_test.log`.

## 6. Performance Evaluation

Performance was evaluated by comparing direct client-to-backend communication with communication through the TCP proxy.

The benchmark program, `experiments/benchmark.py`, records:

- Number of requests
- Concurrency level
- Request latency
- Throughput
- Successful requests
- Response size

Experiments were conducted at concurrency levels of 1, 5, 10, and 20.

Detailed measurements and comparisons are documented in `performance_evaluation.md`.

## 7. Conclusion

The integration and testing work verified the communication path between the client, TCP proxy, backend services, and SDN environment. Functional tests demonstrated traffic filtering and runtime policy changes. Automated tests and performance experiments provided evidence for evaluating the correctness and overhead of the proxy-enabled system.

# CN Proxy

**SDN-Enabled Concurrent TCP Proxy and Traffic Filtering System**

CN Proxy is a computer networking project that implements a concurrent TCP proxy with configurable traffic filtering and dynamic SDN-based network control.

The system allows multiple clients to communicate with simulated services through a TCP proxy. Client requests are inspected against configurable policies, and traffic is either permitted or blocked. Policy decisions can also trigger dynamic SDN flow-rule changes in the network.

The complete system is deployed and evaluated in a **Mininet + Open vSwitch + SDN Controller** environment.

---

## Problem Statement

Develop a TCP proxy through which clients access simulated services. The system should dynamically allow or block traffic according to configurable policies and demonstrate interaction between socket programming and an SDN-controlled network.

The project focuses on:

* Low-level TCP socket programming
* Concurrent client handling
* Application-level request processing
* Configurable traffic filtering
* Dynamic policy changes
* SDN-based traffic enforcement
* Network monitoring and logging
* Performance evaluation

---

## Objectives

The project aims to:

1. Implement a concurrent TCP client-server communication system using low-level sockets.
2. Develop a TCP proxy capable of handling multiple simultaneous clients.
3. Parse and forward application-level requests.
4. Implement configurable allow/deny traffic policies.
5. Deploy the application in a Mininet-based network.
6. Integrate the application with an SDN controller and Open vSwitch.
7. Dynamically install and remove network flow rules based on policy decisions.
8. Support runtime policy changes without restarting the application.
9. Log permitted and blocked requests.
10. Evaluate latency, throughput, concurrency, and proxy overhead under different network conditions.

---

## System Architecture

```text
                         ┌──────────────────────┐
                         │    SDN Controller    │
                         │                      │
                         │  Dynamic Flow Rules  │
                         └──────────┬───────────┘
                                    │
                                 OpenFlow
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │   Open vSwitch       │
                         │                      │
                         │        s1            │
                         └───────┬───────┬──────┘
                                 │       │
                                h1      h2
                              Client   Proxy
                                         │
                                         │ TCP
                                         ▼
                                    ┌──────────┐
                                    │ Services │
                                    │ h3 / h4  │
                                    └──────────┘
```

### Request flow

```text
Client
   │
   │ TCP request
   ▼
TCP Proxy
   │
   ▼
Request Parser
   │
   ▼
Policy Engine
   │
   ├───────────────┐
   │               │
 ALLOW            DENY
   │               │
   ▼               ▼
Forward         Reject
   │
   ▼
Simulated Service

Policy decision
      │
      ▼
SDN Controller
      │
      ▼
OpenFlow
      │
      ▼
Open vSwitch
```

---

## Core Components

### 1. TCP Client

The client establishes a TCP connection with the proxy and sends application-level requests.

### 2. TCP Proxy

The proxy is the central component of the system.

It is responsible for:

* Accepting TCP connections
* Handling multiple clients concurrently
* Receiving and parsing requests
* Evaluating filtering policies
* Forwarding permitted requests
* Rejecting blocked requests
* Communicating relevant policy events to the SDN controller
* Recording connection and filtering information

### 3. Policy Engine

The policy engine determines whether a request should be permitted or blocked.

Example:

```text
Service       Port       Action
--------------------------------
Web           8001       ALLOW
Database      8002       DENY
File          8003       ALLOW
```

Policies are configurable and can be modified while the system is running.

### 4. Simulated Services

The project uses simple TCP services to represent different network applications.

For example:

```text
Service A → Web Service
Service B → Database Service
Service C → File Service
```

These services allow filtering and SDN behavior to be demonstrated in a controlled environment.

### 5. SDN Controller

The controller manages network-level forwarding behavior.

Based on application and policy events, it can:

* Install flow rules
* Remove flow rules
* Drop selected traffic
* Modify forwarding behavior

### 6. Open vSwitch

Open vSwitch provides the programmable switching layer used by the SDN environment.

### 7. Mininet

Mininet provides the controlled virtual network containing:

* Hosts
* Switches
* Links
* SDN controller connectivity

---

## Example Policy

A policy may look conceptually like:

```yaml
rules:
  - service: web
    port: 8001
    action: allow

  - service: database
    port: 8002
    action: deny
```

A request to the web service is forwarded:

```text
Client → Proxy → Web Service
```

A request to the database service is blocked:

```text
Client → Proxy → DENIED
```

The corresponding network behavior can also be enforced through an SDN flow rule.

---

## Dynamic Policy Changes

One of the main features of CN Proxy is runtime policy modification.

For example:

```text
Initial:

Database → ALLOW
```

The policy is changed while the system is running:

```text
Database → DENY
```

The system updates the corresponding network behavior without restarting the proxy.

The reverse operation is also supported:

```text
Database → DENY
        ↓
Database → ALLOW
```

This demonstrates dynamic interaction between the application and SDN-controlled network.

### Implemented behavior

The proxy reloads `config/policies.yaml` when it evaluates a request. The
OS-Ken controller reads the same file periodically (every second by default),
so switch rules also change without a restart or incoming client requests.
The integrated Mininet demo uses a 0.2-second controller polling interval.
Invalid YAML keeps the last valid configuration in each process.

`allow` forwards without inspection, `deny` rejects the request, and `auto`
lets the proxy inspect the backend response and block or redact sensitive
content. `auto` decisions do **not** rewrite the YAML or permanently deny a
service at the switch. Policy edits are made by the operator.

The controller applies IPv4 TCP policy in table 0 before MAC learning in
table 1. With the supplied `default_action: deny`, only allowed/auto service
ports are reachable from the proxy. Direct client TCP access to the backend
is blocked. Content inspection and service-name checks remain in the proxy.

See [the SDN/Mininet runbook](docs/sdn-mininet.md) for commands, flow-table
inspection, layer limitations, and the runtime allow/deny demonstration.

---

## Concurrency

The proxy must support multiple clients simultaneously.

Example:

```text
Client 1 ─┐
Client 2 ─┤
Client 3 ─┼──> TCP Proxy ──> Services
Client 4 ─┤
Client 5 ─┘
```

The implementation will use concurrent connection handling so that one client does not block communication from other clients.

---

## Testing

Run the automated suite using the controller environment:

```bash
.venv-controller/bin/python -m unittest discover -s tests -v
```

These tests cover policy reload, OpenFlow message encoding and table behavior,
controller polling over a real localhost OpenFlow connection, proxy forwarding,
concurrency, and content inspection. They need permission to bind localhost
sockets, but do not need root or a running Open vSwitch.

Run the full Mininet/Open vSwitch smoke test in a root-capable terminal:

```bash
sudo python3 mininet/run_demo.py --smoke-test --switch-name cnptest1 --controller-port 16653
```

The smoke test starts its own controller, server, and proxy, uses a temporary
policy file, checks runtime rule removal/restoration and actual TCP drops, and
stops its own processes and network afterward. It refuses to reuse an existing
bridge or occupied controller port. The scenarios below also describe planned
robustness and performance work.

The system will be tested under multiple conditions.

### Functional tests

* Client-server connectivity
* Proxy forwarding
* Allowed requests
* Blocked requests
* Multiple simultaneous clients
* Runtime policy changes
* Dynamic SDN rule installation/removal

### Robustness tests

The project will evaluate scenarios such as:

* Unauthorized access
* Multiple concurrent connections
* Server unavailability
* Link failure
* High request load

---

## Performance Evaluation

The system will be evaluated using measurable network and application metrics.

### Metrics

* End-to-end latency
* Throughput
* Connection success rate
* Failed requests
* Number of concurrent clients
* Proxy overhead
* Filtering decision time

### Baseline comparison

The proxy-based architecture will be compared against direct client-server communication.

```text
Baseline:

Client ───────────────> Server


CN Proxy:

Client ──> Proxy ──> Server
              │
              └── Policy + SDN
```

This allows the overhead introduced by proxying and policy enforcement to be measured.

---

## Technology Stack

| Component            | Technology          |
| -------------------- | ------------------- |
| Programming Language | Python              |
| Transport            | TCP                 |
| Socket Programming   | Python `socket`     |
| Concurrency          | Python threading    |
| Network Emulator     | Mininet             |
| Virtual Switch       | Open vSwitch        |
| SDN                  | OpenFlow            |
| SDN Controller       | OS-Ken (Ryu fork)   |
| Configuration        | YAML / JSON         |
| Testing              | Python / unittest   |
| Network Analysis     | Wireshark / tcpdump |
| Performance Testing  | Python / iperf3     |

---

## Project Structure

```text
cn-proxy/
│
├── client/
│   └── client.py
│
├── proxy/
│   ├── proxy.py
│   ├── parser.py
│   ├── policy.py
│   ├── classifier.py
│   └── logger.py
│
├── server/
│   ├── server.py
│   └── services.py
│
├── controller/
│   ├── run_controller.py
│   ├── simple_switch.py
│   └── flow_manager.py
│
├── mininet/
│   ├── topology.py
│   └── run_demo.py
│
├── config/
│   └── policies.yaml
│
├── tests/
│   ├── test_sdn.py
│   ├── test_controller_wire.py
│   └── test_proxy_integration.py
│
├── experiments/
│
├── docs/
│   └── sdn-mininet.md
│
└── README.md
```

The exact structure may evolve as implementation progresses.

---

## Development Roadmap

### Phase 1 — TCP Communication

* TCP client
* TCP server
* Application-level message exchange

### Phase 2 — TCP Proxy

* Connection acceptance
* Request parsing
* Request forwarding
* Response forwarding

### Phase 3 — Concurrency

* Multiple simultaneous clients
* Concurrent connection handling
* Error handling

### Phase 4 — Traffic Filtering

* Policy configuration
* Policy evaluation
* Allow/deny decisions
* Logging

### Phase 5 — Mininet Deployment

* Network topology
* Multiple hosts
* Open vSwitch
* Application deployment

### Phase 6 — SDN Integration

* SDN controller
* OpenFlow communication
* Flow-rule installation
* Flow-rule removal

### Phase 7 — Dynamic SDN

* Runtime policy changes
* Dynamic network enforcement
* Application-to-controller communication

### Phase 8 — Robustness

* Failure scenarios
* High concurrent load
* Unauthorized access
* Recovery behavior

### Phase 9 — Performance Evaluation

* Latency measurements
* Throughput measurements
* Concurrent-client experiments
* Baseline comparison

### Phase 10 — Finalization

* Testing
* Documentation
* Architecture diagrams
* Experiment results
* Demonstration

---

## Expected Outcome

At the end of the project, CN Proxy should provide a complete networked application in which:

```text
Multiple Clients
       │
       ▼
Concurrent TCP Proxy
       │
       ▼
Policy Engine
       │
   ┌───┴───┐
   ▼       ▼
ALLOW     DENY
   │
   ▼
Simulated Services
       │
       ▼
SDN-Controlled Network
```

The system should demonstrate that **application-level traffic policies can dynamically influence network-level forwarding behavior in an SDN environment**.

---

## Project Status

**Current status:** Under Development

The implementation is being developed incrementally, beginning with TCP socket communication and progressing toward complete Mininet and SDN integration.

---

## License

License to be decided.

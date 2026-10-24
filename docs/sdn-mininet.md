# SDN and Mininet runbook

The lab keeps the team's existing OS-Ken controller and three-host topology:

| Host | Address | Role |
| --- | --- | --- |
| h1 | 10.0.0.1/24 | TCP client |
| h2 | 10.0.0.2/24 | Proxy on TCP 9000 |
| h3 | 10.0.0.3/24 | Web 8001, database 8002, file 8003 |

All hosts connect to one Open vSwitch bridge using OpenFlow 1.3. The bridge
uses secure fail mode, so it does not fall back to standalone switching when
the controller is unavailable. Existing installed flows can remain effective
during a controller disconnection.

## Prerequisites

Use Linux with Mininet, Open vSwitch, and a running OVS database/switch daemon.
The Mininet runner needs root; the controller and automated localhost tests do
not. Run the following commands from the repository root. The system Python
must import `mininet` and `yaml`. The controller environment must contain the
dependencies from `requirements.txt`.

```bash
python3 -c 'import mininet.topo, yaml'
.venv-controller/bin/python -c 'import os_ken, yaml'
sudo ovs-vsctl show
```

Do not run a second controller on the selected port. The runner refuses an
existing bridge with the requested name, including an already running `s1`.
Choose a different switch name and controller port when another lab exists.

## Start the complete interactive demo

```bash
sudo python3 mininet/run_demo.py
```

This starts the controller on localhost TCP 6653, creates the network, launches
the server on h3 and proxy on h2, and enters the Mininet CLI. It prints the
policy file and the temporary directory containing process logs. The proxy also
uses its existing `logs/cn_proxy.log` file.

Example CLI commands:

```text
mininet> pingall
mininet> h1 python3 -m client.client --host 10.0.0.2 --service web --port 8001
mininet> h1 python3 -m client.client --host 10.0.0.2 --service database --port 8002
mininet> h1 python3 -m client.client --host 10.0.0.2 --service file --port 8003
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

The supplied YAML uses `auto` for all three services. Web/file responses pass;
the database contains deliberately fake sensitive values and is blocked by the
proxy's content inspector. TCP 8002 remains permitted from h2 in `auto` mode
because the proxy must fetch the response to inspect it.

Edit `config/policies.yaml` in another terminal. Keep `default_action: deny`,
and change the database rule from `auto` to `allow`, then `deny`, then `allow`.
After each edit, repeat the database request and inspect the flows:

| Action | Client response | Table 0 |
| --- | --- | --- |
| allow | Database response, without inspection | Permit h2 -> h3 TCP 8002 |
| deny | `DENIED: policy blocks ...` | Remove the permit; backend default drop applies |
| auto, inspection block | `BLOCKED: sensitive content detected ...` | Permit fetching for proxy inspection |
| auto, inspection redact | Response containing `[REDACTED]` | Permit fetching for proxy inspection |

No process restarts are needed. The demo controller polls every 0.2 seconds;
the proxy reloads on the next policy evaluation. The two processes reload
independently, so edits are eventually consistent rather than transactional.
An invalid or missing policy keeps each process's last valid snapshot; startup
requires a valid policy. Edits do not retract responses already received.

Exit the CLI to stop the demo's application processes, controller, and network.
Logs are retained in the printed temporary directory. The runner does not call
the global `mn -c` cleanup command or delete another bridge.

## Automated checks

```bash
.venv-controller/bin/python -m unittest discover -s tests -v
sudo python3 mininet/run_demo.py --smoke-test --switch-name cnptest1 --controller-port 16653
```

The first command runs the suite without Mininet/root. It encodes real OS-Ken
OpenFlow messages, checks the policy/learning pipeline, launches the controller
and exchanges OpenFlow messages over localhost, and forwards proxy requests to
a real TCP backend. The OpenFlow peer in these tests simulates a switch; this
does not substitute for verifying Open vSwitch forwarding.

The second command performs the actual Mininet/OVS check. It creates a
temporary policy rather than editing the team's YAML. It checks ICMP, allowed
responses, direct-client bypass prevention, six concurrent requests, live
allow -> deny -> allow transitions, an actual backend TCP timeout after deny,
and `auto` block/redact behavior. It verifies that unrelated web traffic still
works after denying database access, and that malformed YAML preserves the
last valid policy. Successful completion prints `SDN SMOKE TEST PASSED` and
the switch flows. Root/OVS access is necessary to claim this live check passed.

## Run components separately

Terminal 1:

```bash
.venv-controller/bin/python -m controller.run_controller \
  --policy config/policies.yaml --ofp-port 6653 --poll-interval 1
```

Terminal 2:

```bash
sudo mn --custom mininet/topology.py --topo cnproxy --mac \
  --switch ovsk,protocols=OpenFlow13 --controller remote,ip=127.0.0.1,port=6653
```

Start the existing application commands inside Mininet; use an absolute project
directory if the CLI was opened elsewhere:

```text
mininet> h3 python3 -m server.server > /tmp/cn-server.log 2>&1 &
mininet> h2 python3 -m proxy.proxy --server-host 10.0.0.3 --policy config/policies.yaml > /tmp/cn-proxy.log 2>&1 &
mininet> h1 python3 -m client.client --host 10.0.0.2 --service web --port 8001
```

The controller supports `--listen-host`, `--proxy-ip`, and `--server-ip` for a
custom lab. Host addresses and proxy `--server-host` must match those values.
The integrated runner uses the fixed three-host addresses above. Both processes
must read the same policy file. There is no proxy-to-controller REST endpoint;
coordination currently uses the shared YAML file.

## Flow implementation and boundaries

`controller/flow_manager.py` programs table 0 for policy and table 1 for MAC
learning. Table 0 drops IPv4 TCP to h3 by default and adds higher-priority
exceptions from h2 for allowed/auto ports. Other traffic goes to table 1.
Removing a permit takes effect before learning, including already learned
connections. Updates remove only matching policy entries; reconnect cleanup
uses this application's cookies. The controller reinstalls current policy and
clears its MAC cache on switch reconnect. OpenFlow errors are logged.

The controller reads the shared policy through the additive
`PolicyEngine.snapshot()` API, which returns consistent copies of rules and
settings. It does not rely only on per-rule listeners: those listeners do not
report a change to `default_action`. Poll events and switch events are processed
through the same OS-Ken event queue.

OpenFlow sees IP addresses and TCP ports, not the application's `service=`
field or response contents. If service names share a port, the network allows
it when any name can use it, leaving exact decisions to the proxy. If
`default_action` is `allow` or `auto`, an unlisted service name can use any port,
so h2 receives a general TCP permit to h3. A named deny is still enforced by
the proxy but cannot be distinguished at the switch in that configuration.

The current network policy covers IPv4 TCP in this trusted Mininet lab. It does
not implement UDP/IPv6 filtering, host identity authentication, persistent
content-triggered bans, or automatic policy generation. For direct-client
performance baselines, use a separate baseline network/controller: this
policy-enabled lab intentionally blocks client -> backend TCP.

The controller launcher handles SIGINT using the OS default with OS-Ken's
native backend because the installed 4.2.2 `run_apps()` cleanup assumes a
green thread `.kill()` method. Exiting closes the process's sockets/threads.

References: [OS-Ken OpenFlow 1.3 API](https://docs.openstack.org/os-ken/latest/ofproto_v1_3_ref.html),
[OS-Ken application events](https://docs.openstack.org/os-ken/latest/os_ken_app_api.html),
[Mininet remote-controller walkthrough](https://mininet.org/walkthrough/#using-a-remote-controller).

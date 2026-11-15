# CN Proxy demonstration and viva guide

This is the authoritative operational guide. Run commands from the repository root
(`/home/atharva-dev/cn-proxy` in this checkout). Command blocks contain no prompt
characters. **Terminal 1** runs Mininet; **Terminal 2** is an ordinary Linux shell
in the same directory; **Mininet CLI** means enter the command in Terminal 1 after
startup. Mininet commands prefixed `h1`, `h4`, etc. execute in that host's network
namespace. `sh` executes in the main Linux namespace.

Only use the fake data and local lab addresses below. A database/file “service”
is a demonstration TCP responder, not a database wire protocol or FTP server.
Expected results below are instructions, not claims that the live lab was run.

## A. Setup and startup

### Check prerequisites

Purpose: establish that the correct interpreters, network tools, and controller
packages are available before creating interfaces. Linux, root access, Mininet,
Open vSwitch with its daemons running, Python 3, PyYAML, OS-Ken, and curl are needed.

**Terminal 2:**

```bash
cd /home/atharva-dev/cn-proxy
command -v mn ovs-vsctl ovs-ofctl curl
/usr/bin/python3 -c 'import mininet.topo, yaml; print("host Python ready")'
.venv-controller/bin/python -c 'import os_ken, yaml; print("controller Python ready")'
sudo ovs-vsctl --timeout=3 show
sudo ss -ltnp 'sport = :6653'
```

Expect tool paths, two readiness messages, OVS bridge information, and no existing
listener on 6653. Missing imports mean the corresponding interpreter needs its
packages. A refused OVS socket usually means OVS is stopped; permission denied
means the query needs privileges. Check `systemctl status openvswitch-switch`
on distributions using that service name. These checks prove dependencies and
permissions, not application forwarding.

If dependencies are missing, **Terminal 2**, on an Ubuntu/Debian lab system:

```bash
sudo apt-get update
sudo apt-get install mininet openvswitch-switch python3-venv python3-yaml curl
/usr/bin/python3 -m venv .venv-controller
.venv-controller/bin/python -m pip install -r requirements.txt
```

The system Python sees distribution Mininet packages. The controller venv isolates
OS-Ken and its dependencies; it normally does not see Mininet. Do not launch the
Mininet runner with the venv interpreter or install packages with an unrelated
`pip`. `requirements.txt` lists direct controller dependencies; pip resolves their
transitive dependencies. `matplotlib` is optional, only for regenerating the
historical performance plots (`pip install matplotlib` in a suitable analysis
venv). No package installation is necessary if the checks already pass.

Verify CLI syntax using the actual programs, **Terminal 2:**

```bash
/usr/bin/python3 mininet/run_demo.py --help
/usr/bin/python3 -m proxy.proxy --help
/usr/bin/python3 -m client.client --help
.venv-controller/bin/python -m controller.run_controller --help
```

### Prepare an isolated, reproducible policy

Purpose: make thresholds and initial actions predictable without overwriting
`config/policies.yaml`. The repository policy currently uses AUTO for all three
services and block for sensitive content. The presentation starts with database
ALLOW so its transitions are easy to see. Keep `default_action: deny` throughout.

**Terminal 2:** check that the following temporary filenames do not belong to a
running demo; then create them. Re-running this preparation resets the demo policy.

```bash
/usr/bin/python3 - <<'PY'
from pathlib import Path
import yaml
from proxy.protection import DEFAULTS
policy = {
    'default_action': 'deny',
    'inspection': {'on_sensitive': 'block'},
    'protection': dict(DEFAULTS),
    'rules': [
        {'service': 'web', 'port': 8001, 'action': 'allow'},
        {'service': 'database', 'port': 8002, 'action': 'allow'},
        {'service': 'file', 'port': 8003, 'action': 'auto'},
    ],
}
text = yaml.safe_dump(policy, sort_keys=False)
Path('/tmp/cn-proxy-college-policy.yaml').write_text(text)
Path('/tmp/cn-proxy-college-policy.baseline.yaml').write_text(text)
print(text)
PY
```

Expect the three rules and protection values printed. This baseline is used for
all restoration commands below. These are the actual protection defaults:

| Setting | Default | Meaning |
| --- | ---: | --- |
| requests_per_window | 20 | Admitted connections per source in a sliding window |
| window_seconds | 10 | Sliding request window |
| max_connections_per_client | 5 | Active connections per source, across listeners |
| max_connections_total | 100 | Active connections across all sources/listeners |
| max_tracked_clients | 4096 | Bound on remembered source-IP records |
| block_after_violations | 5 | Strikes before a temporary ban |
| block_seconds | 30 | Ban duration |
| request_timeout_seconds | 5 | Client request deadline; backend socket timeout |
| max_header_bytes | 16384 | Entire HTTP request header including delimiter |

Unknown protection keys, booleans, zero, negative values, and integers over
1,000,000 are invalid. Smaller limits are useful for tests, but the demonstrations
below require these defaults. Existing active connections/bans are not erased by
rewriting YAML. Restart this demo or allow its state to expire when resetting.

### Start five hosts and both listeners

**Terminal 1:**

```bash
cd /home/atharva-dev/cn-proxy
sudo /usr/bin/python3 mininet/run_demo.py --extra-clients 2 --http-port 8080 --policy /tmp/cn-proxy-college-policy.yaml
```

`--extra-clients 2` adds h4/h5 to the original three hosts. `--http-port 8080`
enables HTTP; its default 0 disables HTTP. Custom TCP remains on 9000.
`--policy` makes proxy and controller read the same isolated file. The controller
uses `.venv-controller/bin/python`, listens on loopback 6653, and polls every
0.2 seconds. The runner starts server/proxy in h3/h2, waits for controller,
switch, proxy listeners, and all three local backend listeners, then opens CLI.

Expect `Logs: /tmp/cn-proxy-demo-...`, `Policy: ...`, and the interactive CLI.
Record the exact Logs path. A readiness timeout means startup failed: inspect
`controller.log`, `server.log`, or `proxy.log` in that printed directory. Restore
by exiting this runner and fixing the reported dependency/port before restarting.

**Terminal 2, once started:**

```bash
sudo ovs-vsctl get-controller s1
sudo ovs-vsctl list Controller
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
```

Expect `tcp:127.0.0.1:6653`, the relevant controller's `is_connected: true`, and
flows in tables 0/1. `-O OpenFlow13` selects the protocol used by this controller.
An unrelated controller row may also appear; correlate its target with `s1`.

### Existing lab or stale bridge

The runner refuses an existing requested bridge or occupied controller port.
First exit your existing demo cleanly. To coexist with another lab, use:

**Terminal 1 (only when starting an additional lab is intended):**

```bash
sudo /usr/bin/python3 mininet/run_demo.py --switch-name cnlab2 --controller-port 16653 --extra-clients 2 --http-port 8080 --policy /tmp/cn-proxy-college-policy.yaml
```

Use an independent policy file for independent presentations, and replace `s1`
with `cnlab2` in diagnostic commands. Bridge names must start with a letter,
contain only letters/digits/underscore/hyphen, and be at most 10 characters.
Host application ports are isolated by namespaces; the controller port is not.

For a bridge confirmed to belong to a dead run, **Terminal 2:**

```bash
sudo ovs-vsctl list-ports s1
sudo ovs-vsctl --if-exists del-br s1
```

Deleting a bridge disconnects its ports. It does not stop orphaned application
processes. Inspect their PIDs before using `kill PID` for those specific processes.
`sudo mn -c` is a broad Mininet cleanup that can remove other labs' interfaces,
bridges, and processes; reserve it for an exclusive lab machine after checking
with other users. It is not the default recovery command.

## B. Topology, interfaces, and ports

```mermaid
flowchart LR
  h1["h1 client 10.0.0.1"] --- s1["s1 Open vSwitch\ntable 0 policy → table 1 learning"]
  h4["h4 client 10.0.0.4"] --- s1
  h5["h5 client 10.0.0.5"] --- s1
  s1 --- h2["h2 proxy 10.0.0.2\nTCP 9000 / HTTP 8080"]
  s1 --- h3["h3 backend 10.0.0.3\nweb 8001 / database 8002 / file 8003"]
  c["OS-Ken control plane\n127.0.0.1:6653"] -. OpenFlow 1.3 .-> s1
```

All hosts use `/24`. There is one switch, not a second private backend switch:
client → switch → proxy and proxy → **same switch** → backend are separate TCP
connections. h2 does not route the original client socket to h3.

Purpose: inspect the real topology rather than infer ports from a drawing.
**Mininet CLI:**

```text
nodes
net
links
intfs
h1 ip -br addr
h2 ip -br addr
h3 ip -br addr
h4 ip -br addr
h5 ip -br addr
sh ovs-ofctl -O OpenFlow13 show s1
h2 ss -ltnp
h3 ss -ltnp
pingall
```

Expect h1–h5 and s1, five up links, host `hN-eth0` interfaces, and h2 listening
on 9000/8080, h3 on 8001/8002/8003. Usually s1-eth1 through s1-eth5 connect
h1 through h5 in creation order; use `net` and `ovs-ofctl show` to confirm.
A Linux interface is a virtual Ethernet endpoint. An OpenFlow port number
identifies a switch ingress/egress. A TCP port identifies an application socket;
8001 is not an OpenFlow port.

`pingall` tests pairwise ICMP reachability (and usually exercises ARP). Zero loss
does not prove any service, TCP permit, content policy, HTTP parser, or limiter
works. Missing addresses/links explain ping failures; inspect `links` and the
controller log before trying application tests. These commands change no policy;
continue once addresses/listeners match the table.

## C. Original custom TCP client

Purpose: preserve the original client and demonstrate three service names.
Prerequisite: baseline policy; an idle h1 (no rate-limit test running).
**Mininet CLI:**

```text
h1 python3 -m client.client --host 10.0.0.2 --service web --port 8001
h1 python3 -m client.client --host 10.0.0.2 --service database --port 8002
h1 python3 -m client.client --host 10.0.0.2 --service file --port 8003
```

`--host` is the proxy IP; `--port` is the requested backend service port.
The proxy listener defaults to 9000 (`--proxy-port` changes it on the client).
Expect `Request : service=...;port=...` and `Response: Web/Database/File service:`.
Database includes deliberately fake sensitive values under ALLOW. File AUTO passes
because it is normal content. This proves service/port parsing and actual backend
round trips, including inspection of safe content.

The wire request is UTF-8 `service=web;port=8001` followed by newline. The proxy
also accepts EOF framing, handles fragmented reads, and closes after one response;
the client reads until EOF. It is not an HTTP request. A maximum 1024-byte custom
request is enforced; a nonterminated request reaching that limit is rejected.
Unknown/duplicate fields and invalid ports are errors. Sending ordinary curl to
9000 will fail parsing; use 8080 for HTTP.

If denied, inspect the selected YAML, not just the repository default. If backend
unavailable, inspect `h3 ss -ltnp` and table 0; a backend connection can fail because
of policy as well as a missing service. Restore the baseline using section E.

## D. HTTP forward proxy

Purpose: demonstrate GET, HEAD, source isolation, and destination restrictions.
Prerequisite: baseline, HTTP listener enabled, curl installed. **Mininet CLI:**

```text
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
h4 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8003/
h5 curl --noproxy '' -x http://10.0.0.2:8080 -I --max-time 8 http://10.0.0.3:8001/
```

Expect 200 and web/file text for GET; HEAD returns headers, original representation
`Content-Length`, and no body. `-x` specifies the proxy. `--noproxy ''` disables
curl's environment bypass list, so even local/private URLs use it. `-i` includes
response headers; `-I` sends HEAD; `--max-time 8` bounds the entire curl command.
A fresh curl invocation opens a fresh connection; the proxy closes each response.

Status-only form, **Mininet CLI:**

```text
h1 curl --noproxy '' -x http://10.0.0.2:8080 -sS -o /dev/null -w '%{http_code}\n' --max-time 8 http://10.0.0.3:8001/
```

`-sS` suppresses progress but shows errors; `-o /dev/null` discards the body;
`-w` prints the HTTP status. Expect 200. A curl exit code of 0 can still mean
403/429: read the HTTP status. `000` means no complete HTTP status was received.

Negative tests, **Mininet CLI** (do these from otherwise idle h5):

```text
h5 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.1:8001/
h5 curl --noproxy '' -x http://10.0.0.2:8080 -X POST -i --max-time 8 http://10.0.0.3:8001/
h5 curl --noproxy '' -x http://10.0.0.2:8080 -I --max-time 8 https://10.0.0.3:8001/
```

Expect 403 for the wrong destination, 405 for POST, and CONNECT rejection (405)
for the HTTPS attempt, possibly displayed as curl's tunnel failure. No Internet
traffic is needed. Restore by leaving h5 idle for 36 seconds before later tests.

Only absolute `http://` URLs with the exact configured backend hostname/IP and
one unambiguous explicitly configured service port are accepted. DNS aliases,
userinfo, fragments, origin-form URLs, arbitrary Internet proxying, bodies,
Transfer-Encoding requests, duplicate headers, pipelining, persistent client
connections, CONNECT, HTTPS interception, uploads, WebSockets and other methods
are unsupported. Host is required for HTTP/1.1; routing uses the validated URL,
then rewrites Host for the backend. Hop-by-hop headers and Connection-nominated
headers are removed in both directions. Content is buffered (maximum 1 MiB),
chunked responses are decoded, and the proxy generates its own framing.
AUTO GET requires unencoded UTF-8 and refuses partial 206/Content-Range responses;
HEAD has no response body to inspect and forwards metadata under ALLOW/AUTO.
The sample backend returns the same service text for every path.

If results differ, check `h2 ss -ltnp`, `h5 command -v curl`, selected policy, and
`HTTP_REJECT`/`HTTP_BACKEND_ERROR` in logs. Baseline restoration is in E.

## E. Dynamic policies and OpenFlow

### ALLOW → DENY → ALLOW with fresh requests

Purpose: change application and switch policy without restarts. Keep default DENY,
web ALLOW, file AUTO, and database initially ALLOW. Use the isolated demo file.

**Terminal 2:**

```bash
nano /tmp/cn-proxy-college-policy.yaml
```

Find `service: database`, leave `port: 8002`, change only its `action` to `deny`.
Keep indentation consistent and use spaces, not tabs. In nano press Ctrl+O, Enter
to save, then Ctrl+X. Wait about one second for the controller poll. **Mininet CLI:**

```text
h1 python3 -m client.client --host 10.0.0.2 --service database --port 8002
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8002/
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Expect `DENIED: policy blocks database on port 8002`, HTTP 403, and removal of the
h2→h3 port-8002 permit. Change database back to `allow`, save, wait one second,
and repeat: expect database text/200 and the permit restored. This proves
independent hot reload and flow revocation despite previously learned forwarding.

For an exact reproducible edit instead of nano, **Terminal 2** (change `'deny'`
to `'allow'` for the second step):

```bash
/usr/bin/python3 - <<'PY'
from pathlib import Path
import yaml
p = Path('/tmp/cn-proxy-college-policy.yaml')
d = yaml.safe_load(p.read_text())
for rule in d['rules']:
    if (rule['service'], rule['port']) == ('database', 8002):
        rule['action'] = 'deny'
tmp = p.with_suffix('.new')
tmp.write_text(yaml.safe_dump(d, sort_keys=False))
tmp.replace(p)
PY
```

Atomic replacement avoids a partially written valid document. Application reload
happens on requests/admission, controller reload via 0.2-second polling. These
are separate processes, so transitions are eventually consistent, not atomic.
Requests already being handled retain their captured action/settings; edits do
not retract previously delivered data.

### Observe a finite continuous loop

Purpose: see responses change while editing. **Mininet CLI:**

```text
h1 sh -c 'for i in $(seq 1 60); do date +%T; curl --noproxy "" -x http://10.0.0.2:8080 -sS -o /dev/null -w "%{http_code}\n" --max-time 3 http://10.0.0.3:8002/; sleep 1; done'
```

This prints time/status once per second for at most 60 requests, below the
20-per-10-second default. Edit in Terminal 2 during the loop: 200→403→200.
Ctrl+C in Terminal 1 stops it early. Do not combine this loop with h1 bursts.
Policy DENY itself does not add violations. Stop it before the next demonstration.

**Terminal 2, watch switch changes:**

```bash
sudo watch -n 1 'ovs-ofctl -O OpenFlow13 dump-flows s1'
```

Ctrl+C stops watch. `n_packets`/`n_bytes` show matching traffic; `duration` is time
since installation, not ban duration. A reinstalled permit has a new duration and
counters. Cookies identify this application's rules, not client identities:

| Table / priority | Cookie | Match and action |
| --- | --- | --- |
| 0 / 200 | 0x434e000000000001 | h2→h3 IPv4 TCP allowed/AUTO service port; goto 1 |
| 0 / 150 | same | General h2→h3 TCP permit only if default ALLOW/AUTO |
| 0 / 100 | same | All other IPv4 TCP destined for h3: drop |
| 0 / 0 | same | Other traffic: goto 1 |
| 1 / 10 | 0x434e000000000002 | Learned ingress/source/destination MAC: output; idle timeout 60s |
| 1 / 0 | same | Miss: send full packet to controller |

Priorities compare within a table. A table-1 learned output can never override a
table-0 drop: a dropped packet never reaches table 1. Policy entries have no
idle expiry. Strict cookie/match/priority deletes revoke permits without deleting
learning flows; reconnect initialization removes entries with this project's
cookies. Barrier messages order processing; they do not prove successful packet
forwarding. Controller `OPENFLOW_ERROR` logs need attention.

ALLOW and AUTO can have identical flows: both must let h2 fetch the response;
Python alone inspects text. OpenFlow sees IP/TCP fields, not service names or
response sensitivity. With default ALLOW/AUTO, an unknown custom service can
reach any port on the configured backend; exact service denials remain proxy
checks. HTTP still requires one explicit service rule per port. Shared ports
are permitted at the switch if any service on the port is allowed/AUTO. Keep the
baseline default DENY for the most restrictive teaching demonstration.

**Restore, Terminal 2:**

```bash
cp /tmp/cn-proxy-college-policy.baseline.yaml /tmp/cn-proxy-college-policy.new
mv /tmp/cn-proxy-college-policy.new /tmp/cn-proxy-college-policy.yaml
sleep 1
```

If a saved change does nothing, verify the runner's printed Policy path, check
`POLICY_RELOAD_FAILED`, and dump flows. Editing the repository YAML does not change
a runner launched with the isolated policy path.

## F. Response inspection and invalid YAML

Purpose: contrast allow, deny, and AUTO using only `server/services.py` fake
records (test card, fake SSN, example email, fake password). Start from baseline.

**Terminal 2:**

```bash
nano /tmp/cn-proxy-college-policy.yaml
```

Set database `action: auto`, retain `inspection: on_sensitive: block` as the
existing properly indented nested mapping, save and wait one second.
**Mininet CLI:**

```text
h1 python3 -m client.client --host 10.0.0.2 --service database --port 8002
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8002/
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8003/
```

Expect custom `BLOCKED: sensitive content detected ...`, HTTP 403 for database,
and HTTP 200 with normal file text. This proves AUTO depends on the response,
not a blanket service-name classification. The backend is fetched before blocking.
Change `on_sensitive: block` to `on_sensitive: redact`, save, repeat database
commands: expect `[REDACTED]`, HTTP 200, and no original matched values.
ALLOW exposes the fake original values without inspection; DENY rejects without
fetching a response. Repeat these actions using section E if presenting all modes.

The classifier is deterministic regular expressions plus checksums, not machine
learning. Any high-severity finding is sensitive: 13–19-digit Luhn-valid card
numbers, SSN-shaped strings, 12-digit Verhoeff-valid Aadhaar-shaped numbers
starting 2–9, private-key BEGIN markers, AKIA-shaped AWS keys, or non-whitespace
values following password/passwd/pwd/secret/api-key/token-style assignments.
Emails and Indian mobile-shaped numbers are low severity; at least **three low
findings**, not necessarily distinct identities, trigger sensitivity. When sensitive,
redaction replaces all matched spans, merging overlaps. A private-key rule matches
the BEGIN marker, not the whole key block: this is not a complete secret scrubber.
Formats can be missed, benign text can match, and no guarantee of complete DLP is
made. Labels rather than matched values appear in inspection logs.

### Retain the last valid policy on error

Prerequisite: first get a successful redacted response under AUTO/redact.
**Terminal 2:**

```bash
cp /tmp/cn-proxy-college-policy.yaml /tmp/cn-proxy-college-policy.valid.yaml
printf 'rules: [\n' > /tmp/cn-proxy-college-policy.yaml
sleep 1
```

**Mininet CLI:**

```text
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8002/
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Expect the same redacted HTTP 200, existing forwarding permits, and
`POLICY_RELOAD_FAILED | keeping previous rules` in proxy/controller logs.
This proves rejected reloads retain each process's last valid snapshot. Startup
with invalid YAML fails; do not restart while the file is broken. Duplicate YAML
keys, unknown settings, duplicate service/port pairs, and non-integer/out-of-range
rule ports also invalidate a reload. Missing files retain the previous snapshot.

**Restore, Terminal 2:**

```bash
cp /tmp/cn-proxy-college-policy.valid.yaml /tmp/cn-proxy-college-policy.new
mv /tmp/cn-proxy-college-policy.new /tmp/cn-proxy-college-policy.yaml
sleep 1
```

Check redaction again, then use E's baseline restoration for the next section.
Failure diagnostics: `h3 ss -ltnp`, proxy inspection labels, and table-0 permits.
A 502 for non-UTF-8/encoded/partial content is an inspection limitation, not a
sensitive-content verdict. HEAD metadata is not scanned.

## G. Abnormal-client protection

These are finite local tests. Identity is the accepted socket's source IP, so
h1/h4/h5 are independent. Clients behind NAT share one identity. IP spoofing,
host authentication, distributed attacks, and general intrusion detection are
outside this rule-based protection. Admission counts connections on both
listeners, including empty/readiness connections, not only successful requests.

Strikes accumulate for rate/concurrency excess, malformed/restricted HTTP
requests, and request timeouts. Five strikes cause a 30-second ban; requests
during a live ban do not extend it. Policy DENY and AUTO block are service/content
decisions rather than client bans. Idle source records are pruned after the larger
of window/block time when inactive and not banned. Otherwise strikes can persist;
a quiet interval/reset matters when repeating a test.

### Rate limit, isolation, and recovery

Purpose: reliably exceed 20 admissions within 10 seconds, then cross five
violations and observe a temporary block. Prerequisites: restore baseline,
stop loops, no other h4 traffic, no global exhaustion. Clear prior h4 state:
**Mininet CLI:**

```text
h4 sleep 36
h4 python3 -c 'import http.client,time; start=time.monotonic(); statuses=[]; bodies=[]; exec("for i in range(26):\n c=http.client.HTTPConnection(\"10.0.0.2\",8080,timeout=2)\n c.request(\"GET\",\"http://10.0.0.3:8001/\",headers={\"Host\":\"10.0.0.3:8001\"})\n r=c.getresponse(); b=r.read().decode(); statuses.append(r.status); bodies.append(b); print(i+1,r.status,b.strip()); c.close()"); elapsed=time.monotonic()-start; print("elapsed",elapsed); assert elapsed < 10, "Burst too slow; reset and repeat"; assert statuses[:20] == [200]*20; assert statuses[20:] == [429]*6; assert "temporarily blocked" in bodies[-1]'
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
h5 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
h4 python3 -m client.client --host 10.0.0.2 --service web --port 8001
```

The Python command makes exactly 26 fresh HTTP requests: first 20 should be 200,
next five 429 `request rate exceeded`, final one 429 `temporarily blocked`.
The elapsed assertion explicitly invalidates a slow burst. h1/h5 must still return
200 while h4's custom listener returns `REJECTED: temporarily blocked`, proving
shared protection and client isolation. Execute those last three checks promptly,
within the ban interval. Rejected requests have `Retry-After: 1` as an advisory
retry interval, not the exact remaining ban duration.

**Mininet CLI, recovery:**

```text
h4 sleep 31
h4 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
```

Expect 200 without restarting. The original request window has also elapsed.
This proves automatic recovery, not removal of a permanent firewall rule. Diagnose
unexpected early 429 using `CLIENT_REJECT` reasons and printed baseline thresholds;
wait 36 quiet seconds before retrying, including after failed attempts. If the
burst is too slow, investigate load rather than claiming it demonstrated a ban.

### Malformed, oversized, and slow requests

Purpose: bound invalid requests before backend forwarding. Baseline settings;
use idle h4 after 36 seconds. Each command prints the HTTP response.
**Mininet CLI:**

```text
h4 sleep 36
h4 python3 -c 'import socket; s=socket.create_connection(("10.0.0.2",8080),timeout=8); s.sendall(b"GET http://10.0.0.3:8001/ HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n"); print(s.recv(4096).decode()); s.close()'
h4 python3 -c 'import socket; s=socket.create_connection(("10.0.0.2",8080),timeout=8); s.sendall(b"GET http://10.0.0.3:8001/ HTTP/1.1\r\nHost: a\r\nX-Large: "+b"x"*16384+b"\r\n\r\n"); print(s.recv(4096).decode()); s.close()'
h4 python3 -c 'import socket,time; s=socket.create_connection(("10.0.0.2",8080),timeout=8); start=time.monotonic(); s.sendall(b"GET "); print(s.recv(4096).decode()); print("seconds",round(time.monotonic()-start,1)); s.close()'
h4 python3 -c 'import socket; s=socket.create_connection(("10.0.0.2",9000),timeout=8); s.sendall(b"service=web;port=nope\n"); print(s.recv(4096).decode()); s.close()'
```

Expect 400 `Duplicate headers`, 431 `Request headers too large`, 504 after about
5 seconds, and custom `ERROR: Port must be an integer`. These four violations
stay below the five-strike ban threshold from a clean initial state. The 16384-byte
limit includes request line, headers, and terminator, so the oversized value alone
necessarily exceeds it. A raw TCP receive is allowed to return a fragment; if only
a header fragment prints, consult the log for the rejection reason.
Custom requests have a separate 1024-byte bound. Custom slow requests log
`CLIENT_TIMEOUT` and close without an HTTP response. The HTTP client deadline is
absolute: sending one byte periodically does not restart it.

These tests prove validation and resource reclamation, not detection of every
attack. A 429 instead of the expected error means previous state was not cleared.
Observe `HTTP_REJECT`, `HTTP_TIMEOUT`, `INVALID`, or `CLIENT_TIMEOUT` and restore
by waiting 36 quiet seconds. YAML restoration alone does not clear protection state.

### Concurrent connections and shared listeners

Purpose: show five open connections from h4 consume its slots on **both**
listeners without exhausting the 100-slot global capacity. Idle h4, baseline,
no other traffic from it. **Mininet CLI:**

```text
h4 sleep 36
h4 python3 -c 'import socket,time,http.client; held=[socket.create_connection(("10.0.0.2",9000),timeout=2) for _ in range(5)]; time.sleep(0.3); c=http.client.HTTPConnection("10.0.0.2",8080,timeout=2); c.request("GET","http://10.0.0.3:8001/"); r=c.getresponse(); body=r.read().decode(); print(r.status,body); c.close(); [s.close() for s in held]; assert r.status==429 and "concurrent" in body'
h4 sleep 1
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
h4 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
```

Expect 429 `too many concurrent connections`, then 200 from h1 and h4 after held
sockets close. These held sockets send no data; the test finishes before their
5-second deadline. A rate rejection means the source was not idle. A timeout
means the five-second prerequisite was missed. Close sockets on interruption;
otherwise the request deadline clears them. Wait 36 seconds before repeating.

Global capacity rejection says `proxy connection capacity reached` (HTTP 429 or
custom REJECTED); it applies even to innocent clients while all 100 slots are
occupied. Tracked-source exhaustion says `client capacity reached`. Neither adds
strikes to a new innocent source. Do not confuse these with per-client isolation.
The default-sized global saturation is covered by unit-level admission tests,
not required as a live 100-connection presentation burst. Backend demo responders
also cap their workers at 100 per service port and close excess connections.

## H. Network enforcement

Purpose: prove a switch drop, rather than a dead service or application denial.
Restore baseline, wait one second, use an idle h1. **Mininet CLI:**

```text
h3 ss -ltnp
h2 python3 -c 'import socket; s=socket.create_connection(("10.0.0.3",8001),timeout=2); print("backend reachable from proxy"); s.close()'
sh ovs-ofctl -O OpenFlow13 dump-flows s1
h1 python3 -c 'import socket,time; start=time.monotonic(); exec("try:\n s=socket.create_connection((\"10.0.0.3\",8001),timeout=2); s.close(); print(\"FAIL: direct connection succeeded\")\nexcept socket.timeout:\n print(\"Expected timeout: direct TCP blocked\",round(time.monotonic()-start,1))\nexcept OSError as e:\n print(\"Not proof of a drop:\",e)")'
sh ovs-ofctl -O OpenFlow13 dump-flows s1
h1 curl --noproxy '' -x http://10.0.0.2:8080 -i --max-time 8 http://10.0.0.3:8001/
```

Expect h3 listening, h2 backend reachability, h1 direct TCP timing out, the
priority-100 backend drop counter increasing, and HTTP 200 through proxy. The
priority-200 permit matches only source h2, so h1 cannot use it. A connection
refusal means a reachable TCP stack sent RST, often a missing service; it is not
proof of a switch drop. Timeout alone is also insufficient without the listener,
positive h2 test, flow match and counter evidence. Check `pingall`/links if those
controls fail. Repeat with h4/h5 or ports 8002/8003 if desired, preserving the
correct policy for successful proxy controls.

No manual flow insertion is needed. Adding an ad hoc permit would invalidate the
configuration-driven demonstration. Enforcement covers IPv4 TCP destined for h3,
including ports not running services, but does not filter UDP/IPv6. Source-IP
matches trust host identity in this lab; a malicious privileged host spoofing h2
is outside this design. ARP and ICMP proceed to learning, so ping can succeed
while direct TCP is dropped. Existing flows may continue while a controller is
disconnected; OVS secure fail mode prevents standalone fallback, not immediate
revocation of all previously installed permits. Restore baseline; no rules need
manual cleanup.

## I. Logs, troubleshooting, verification, and shutdown

The runner prints its actual unique directory, e.g. `/tmp/cn-proxy-demo-abc123`.
It contains controller.log, server.log, and proxy.log. Find candidate directories
if the startup line scrolled away, **Terminal 2:**

```bash
ls -td /tmp/cn-proxy-demo-*/
```

Select the directory for **this** run, not blindly the newest on a shared machine.
Replace the example below with that exact directory, **Terminal 2:**

```bash
tail -F /tmp/cn-proxy-demo-abc123/controller.log /tmp/cn-proxy-demo-abc123/proxy.log
```

Ctrl+C stops tail. The proxy additionally writes `logs/cn_proxy.log`, recreating
the directory when absent. The runner's separate stdout/stderr logs are better
for correlating one run; `logs/cn_proxy.log` can include previous runs.

| Event/symptom | Meaning / useful diagnostic |
| --- | --- |
| POLICY_CHANGED | Service/port action changed |
| POLICY_SETTINGS_CHANGED | Default, inspection, or protection settings changed |
| POLICY_RELOAD_FAILED | Last valid policy retained; inspect exact Policy path/YAML |
| POLICY_SENT | Controller sent changed network policy; inspect switch counters too |
| OPENFLOW_ERROR | Switch rejected a controller message; inspect controller.log |
| HTTP_FORWARD / HTTP_INSPECT | Forwarding status / AUTO verdict |
| HTTP_AUTO_BLOCK / AUTO_BLOCK / AUTO_REDACT | Content verdict and labels |
| HTTP_REJECT | HTTP validation/error status and reason |
| CLIENT_REJECT | Rate, concurrency, ban, global or tracked-client capacity reason |
| HTTP_TIMEOUT / CLIENT_TIMEOUT | Incomplete client or HTTP backend timed out |
| HTTP_BACKEND_ERROR / SERVER_ERROR | Missing/broken backend or network path |
| 502 | Upstream failure, oversized/truncated/invalid response, or uninspectable content |
| 504 | HTTP request deadline or backend socket/body timeout |
| curl missing | `h1 command -v curl`; install system curl |
| No HTTP listener | `h2 ss -ltnp`; start with --http-port 8080 |
| Wrong interpreter | Run the import checks in A with the exact interpreter |
| Disconnected controller | `sudo ovs-vsctl list Controller`; inspect target and controller.log |
| Stale topology | `sudo ovs-vsctl show`, `net`, `links`; avoid broad cleanup |
| Wrong service port | `h3 ss -ltnp`; custom --port is backend, --proxy-port is listener |

Response inspection logs include labels, not matched secret values. Backend verbose
custom-request logging and custom parser errors are still teaching diagnostics;
do not send real secrets. `CN_SERVER_QUIET=1` suppresses normal backend request
prints; `CN_PROXY_LOG_LEVEL=WARNING` reduces proxy output for benchmarks.
Runtime log rotation is not implemented, so monitor disk use on long runs.

### Automated verification

**Terminal 2 (no root required):**

```bash
.venv-controller/bin/python -m unittest discover -s tests -v
```

These tests use local TCP sockets, multiple loopback source IPs, policy files,
real OS-Ken message encoders, simulated switch state, and a real controller
process connected to an OpenFlow-speaking test peer. They cover policy reload,
framing, inspection, limits, expiry, shutdown, and flow generation. They do not
prove Linux namespace links, kernel OVS forwarding, or live direct-access drops.
The system Python lacks OS-Ken in this environment and cannot run the full suite.

**Terminal 2, after exiting the interactive demo, with root/OVS access:**

```bash
sudo /usr/bin/python3 mininet/run_demo.py --smoke-test --switch-name cnptest1 --controller-port 16653 --extra-clients 2 --http-port 8080
```

The smoke runner creates a private temporary policy, checks ping, custom backend
responses, direct-access drop, six concurrent custom clients (its policy explicitly
raises the per-client connection limit to 10), runtime deny despite learned
forwarding, allow restoration, AUTO block/redact, and invalid-YAML retention.
It prints `SDN SMOKE TEST PASSED` only on success and cleans up its network.
HTTP listener readiness is checked, but this smoke routine does not itself run
HTTP GET/HEAD or the full abnormal-client sequence; execute D/G manually too.

Review verification on 2026-11-15: the final nonprivileged suite results are
recorded in `docs/test_results.md`. Live Mininet/OVS execution was unavailable:
`sudo -n true` required interactive authentication and an unprivileged OVS query
returned Permission denied. The privileged smoke test and manual five-host
presentation are remaining checks, not recorded successes.

### Shutdown and restoration

Stop the request loop, watch, and tail with Ctrl+C in their respective terminals.
Restore E's baseline if continuing another demo. **Mininet CLI:**

```text
exit
```

The runner terminates only its recorded child processes, waits up to four seconds
before killing a child that has not stopped, stops its Mininet network and closes
log files. The proxy handles SIGTERM by closing listeners, waking client reads,
and waiting for its workers; a long backend operation can exceed the runner's
four-second grace. SIGINT also requests listener cleanup. Backend socket timeouts
bound inactivity; HTTP header parsing uses standard-library per-read timeouts,
so this is not a production-grade absolute deadline on every upstream phase.
Custom backend reads and HTTP response-body reads have total deadlines.

**Terminal 2:**

```bash
sudo ovs-vsctl --timeout=3 br-exists s1
sudo ss -ltnp 'sport = :6653'
git diff -- config/policies.yaml
```

`br-exists` should return exit code 2 (bridge absent), no runner controller listener
should remain, and the repository YAML should have no new changes from this guide.
If interrupted cleanup failed, inspect PIDs/logs before stopping only this demo's
remaining processes. Keep logs as evidence if useful. Once no process uses them,
remove only the named temporary policy/baseline/valid backups created in A/F.
Do not remove another run's temporary directory. Your original repository policy
was never edited by these instructions.

## J. Reliable 5–10 minute presentation

Prepare dependencies and baseline beforehand. Keep Terminal 2 open on the policy
and logs; start with no old h4 connections or bans. Stop after any unexpected
result and use its diagnostic rather than narrating an unverified success.

| Time | Action | Expected evidence / sentence to say |
| --- | --- | --- |
| 0–1 min | Start A; show `net`, listeners, `pingall` | Five hosts, services ready. “Ping checks reachability, not application policy.” |
| 1–2 min | C custom web/database/file | Service text. “This newline-framed protocol is the original TCP client.” |
| 2–3 min | D GET, HEAD, h4/h5 GET | 200, HEAD no body. “The proxy opens a separate backend connection.” |
| 3–4 min | E database allow→deny→allow; dump flows | 200→403→200, permit disappears/returns. “Table 0 enforces policy before MAC learning.” |
| 4–5 min | F AUTO/block then redact | 403 then 200 with redacted fake values. “AUTO needs the response before deciding.” |
| 5–7 min | Restore baseline; G h4 burst, h1/h5 controls, recovery | h4 429, others 200, h4 200 after 31s. “Limits are shared across listeners but isolated by source IP.” |
| 7–8 min | H direct-access negative and proxy control | Direct timeout with drop counter; proxy 200. “Clients cannot bypass the proxy over IPv4 TCP.” |
| 8–9 min | F malformed YAML then restore; show logs | Last good behavior retained. “A broken edit cannot replace the valid snapshot.” |
| 9–10 min | Stop loops and exit | Runner cleans its own network. “This is a bounded teaching proxy with explicit protocol limits.” |

For five minutes, shorten explanations and show malformed/connection-limit tests
and their regression results during viva. Do not run every G test while keeping
the ten-minute claim; the full negative-test catalogue is longer.

## Project explanation and viva preparation

### Overview and problem

CN Proxy mediates access to simulated services and demonstrates application
policy plus SDN enforcement. Without mediation, clients could contact backend
services directly, consume resources, or receive sensitive response text. The
proxy validates requests, applies allow/deny/AUTO, limits each source, and logs
outcomes. The controller independently installs rules ensuring the expected
IPv4 TCP path through h2. It is a forward proxy configured by the client, not a
transparent interception device.

### Request/response walkthrough

1. h1 opens TCP from an ephemeral client port to h2:9000 or h2:8080. ARP resolves
   local Ethernet destinations; the switch learns source MAC locations in table 1.
2. The shared protection object admits/rejects the source before starting a worker.
3. The worker reads a bounded, framed request with a deadline. HTTP routing is
   restricted to the configured backend and an explicit unambiguous port.
4. A consistent YAML snapshot supplies action/settings. DENY replies locally;
   ALLOW/AUTO open a new socket from an ephemeral h2 port to h3's service port.
5. The backend-bound packets traverse s1 table 0. Only h2's allowed/AUTO ports
   proceed to table 1; direct h1/h4/h5 TCP is dropped.
6. h3's demo responder sends text. The proxy buffers up to 1 MiB. ALLOW skips
   inspection; AUTO scans GET/custom text, then passes, blocks, or redacts it.
7. HTTP gets a separately framed response; custom TCP reads response until EOF.
   The proxy closes sockets and releases the source/global connection slot.
8. A later request sees a valid YAML edit; the controller polls separately and
   updates permits without removing the table-1 learning entries.

### Technologies

| Technology | Actual role |
| --- | --- |
| Python sockets/threading | Blocking TCP I/O; admitted connection workers |
| http.client / urllib.parse | Backend HTTP parsing and destination URL parsing |
| PyYAML | Validated policy load and hot reload |
| Regex + Luhn/Verhoeff | Deterministic response sensitivity rules |
| Mininet | Linux network namespace hosts and virtual Ethernet topology |
| Open vSwitch | Kernel/userspace virtual switching data plane |
| OpenFlow 1.3 | Controller-to-switch matches, actions, table transitions and updates |
| OS-Ken | Controller event handling, flow programming and learning logic |
| curl | HTTP client explicitly configured to use the forward proxy |
| unittest | Local socket, policy, controller-wire and simulated flow tests |

### Current limitations and realistic improvements

This project does not implement production HTTP compliance, TLS interception,
CONNECT tunneling, HTTP/2, uploads, real database/file protocols, machine learning,
or comprehensive intrusion detection. It buffers bounded responses rather than
streaming; HTTP AUTO only supports unencoded UTF-8 complete bodies, and HEAD
metadata is not inspected. Source identity assumes a trusted lab; UDP/IPv6 and
source spoofing are not comprehensively controlled. Policy reloads across processes
are eventually consistent. A valid but intermediate YAML document can still be
accepted during a non-atomic editor save; atomic replacement is recommended.
There is no authenticated management API, persistent bans, distributed coordination,
log rotation, guaranteed whole-key redaction, or complete absolute deadline over
all HTTP backend parsing phases. Global overload can affect all clients.

Useful next steps are stronger configuration transactions, broader HTTP conformance
tests, verified host/port identity at the switch, UDP/IPv6 policy, log rotation,
more complete secret patterns, and tighter upstream deadline enforcement. HTTPS
support requires a deliberate design: CONNECT tunneling alone carries encrypted
data and does not make response inspection possible.

### Concise viva questions and answers

| Question | Answer |
| --- | --- |
| What is a proxy server / forward proxy? | An intermediary that makes an upstream request for a client. Here clients explicitly select h2 as their forward proxy. |
| Custom TCP versus HTTP proxy? | Both use TCP sockets; 9000 understands `service=...;port=...` plus newline, while 8080 understands restricted HTTP GET/HEAD and absolute URLs. |
| Why are client ports different each time? | The OS chooses ephemeral source ports; destination 9000/8080 selects the proxy listener. The upstream connection has its own ephemeral port. |
| Is one send equal to one recv? | No. TCP is an ordered byte stream; requests may be fragmented/coalesced. Newline/EOF frames custom requests, CRLFCRLF ends HTTP headers, and lengths/chunks/close frame HTTP responses. |
| How are concurrent clients served? | Separate threads for admitted connections. Locks protect shared source counters and policy snapshots; admission bounds worker creation. |
| Rate versus connection limit? | Rate counts admissions in the last 10 seconds; connection limit counts simultaneously active sockets. A slow client can exhaust slots without sending many requests. |
| What does the timeout do? | Releases stalled request work rather than holding a socket forever. Client reads use an absolute deadline; backend phases have the bounds described in I. |
| How does temporary blocking work? | Five violations create a 30-second source-IP ban in memory. It expires automatically; it is not an OpenFlow ban rule. |
| Per-client versus global limits? | Five active / 20 per window for each IP, but 100 active total. Per-client isolation cannot promise availability if global capacity is exhausted. |
| Why 200 / 400 / 403? | 200: sample backend success. 400: invalid HTTP syntax/framing. 403: policy/destination denied or sensitive response blocked. |
| Why 405 / 429 / 431? | 405: unsupported method. 429: protection admission rejected. 431: request headers exceed configured byte limit. |
| Why 502 / 504? | 502: unavailable, invalid, truncated, oversized or uninspectable backend response. 504: HTTP client/backend timeout. Custom TCP uses textual errors rather than HTTP codes. |
| GET versus HEAD? | GET returns the body; HEAD returns representation headers without a body. This AUTO implementation does not fetch a separate GET to inspect HEAD content. |
| HTTP versus HTTPS? | HTTPS encrypts HTTP in TLS. Reading ciphertext cannot reveal sensitive text; this project rejects CONNECT rather than intercepting TLS. |
| What does Mininet virtualize? | Host network stacks with Linux namespaces and virtual Ethernet links on one machine; processes/filesystem are not full separate VMs. |
| What is OVS? | The virtual switch forwarding packets using installed rules; it is the data plane. |
| What is OS-Ken / OpenFlow? | OS-Ken runs controller logic; OpenFlow 1.3 carries matches, instructions, actions and switch events. |
| Control plane versus data plane? | The controller decides/installs forwarding policy; OVS executes it for packets. Content decisions also occur in the proxy application. |
| What does a flow match? | Here Ethernet/IP/TCP fields, such as IPv4 destination, source, protocol and destination TCP port; learning matches ingress/MACs. |
| What do priority, counter, cookie, duration mean? | Priority selects a matching entry within one table; counters measure matches; cookie identifies owned rules; duration measures entry lifetime. |
| Why multiple tables? | Policy table 0 gates access before learning table 1 outputs frames. Learned forwarding cannot jump backward to override a drop. |
| What are ARP, ICMP, TCP? | ARP resolves IPv4 neighbors to MAC addresses; ICMP supports diagnostics such as ping; TCP provides reliable ordered byte streams for these applications. |
| Why can ping pass but curl fail? | ICMP permission does not establish a TCP listener, allowed TCP flow, valid HTTP request, or successful application policy. |
| How does YAML reload work? | File modification time triggers parsing/validation. Proxy loads on use and controller polls. Invalid runtime edits retain the last valid snapshot; invalid startup fails. |
| Request validation versus response inspection? | Validation checks framing/method/destination before forwarding; inspection scans returned text under AUTO. |
| Block versus redact? | Block withholds the whole body; redact replaces detected spans when the sensitivity threshold is met and returns the modified text. |
| How is direct backend access prevented? | Table 0 drops IPv4 TCP to h3 except h2 permits for allowed/AUTO ports; the proxy enforces finer service-name rules. |
| Does the database label mean SQL support? | No. Port 8002 returns repository fake text; the label selects a demonstration responder. |
| What do tests prove? | Local socket behavior, policy validation/reload, limits, classification, controller messages and simulated pipeline behavior. Only privileged Mininet/OVS traffic checks prove the real kernel forwarding path. |
| How does this differ from production? | Restricted protocols, trusted source IPs, small in-memory limits, buffered bodies, simple rules and no deployment hardening, complete DLP, TLS mediation or availability guarantees. |

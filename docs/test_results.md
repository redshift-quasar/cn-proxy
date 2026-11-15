# Verification results

## Review verification: 2026-11-15

Executed from the repository root:

```bash
.venv-controller/bin/python -m unittest discover -s tests -v
```

**39 tests passed.** Coverage includes the original client with the actual demo
responder, GET/HEAD, hot reload, AUTO block/redact, independent source addresses,
shared listener limits, ban expiry with an injected clock, global admission
capacity, malformed/oversized/incomplete requests, absolute custom-backend read
deadlines, truncated/ambiguous/chunked backend framing, bodyless statuses,
rejection of partial AUTO responses, strict YAML retaining the last valid policy,
listener shutdown, real controller OpenFlow exchanges, and simulated flow-table
revocation despite existing learned forwarding. Backend HTTP timeouts do not add
misconduct strikes to the client. The backend-timeout branch test injects a socket
timeout; the incomplete-client timeout test uses a real socket/deadline.

Additional executed checks passed:

- Actual proxy CLI starts TCP and HTTP listeners and exits with status 0 on
  SIGTERM while incomplete clients are connected.
- Actual client/proxy/runner CLIs reject invalid ports, duplicate listener ports,
  and an invalid bridge name with argparse status 2.
- All four CLI help outputs match the syntax documented in DEMO.md.
- Controller venv `pip check`: no broken requirements.
- Compilation of 10 Python demo snippets, local documentation link validation,
  and `git diff --check`.

The first attempt with system `python3` ran application tests but failed to import
both SDN test modules because OS-Ken is only installed in the controller venv.
Use the venv command above for the complete suite.

### Remaining privileged checks

Live Mininet/OVS tests were **not run** in this review:

- `sudo -n true`: `sudo: interactive authentication is required`.
- `ovs-vsctl --timeout=2 show`: database connection failed `(Permission denied)`.

Run DEMO.md's privileged smoke command and manual five-host checks on the lab
machine. Actual namespace forwarding, switch drop counters, live service-policy
revocation with learned flows, and h1/h4/h5 presentation timing remain to verify.
The nonprivileged OpenFlow peer and flow simulator are not a real OVS datapath.
The existing historical evidence below is preserved and does not establish that
the current changes passed a new privileged run.

### Cleanup and preservation

- Removed `cn-proxy-fixed.patch` after `git apply --reverse --check` established
  it was fully applied. A safety copy is outside the repository at
  `/tmp/cn-proxy-review-backup/cn-proxy-fixed.patch`.
- Removed disposable source-tree `__pycache__` directories; future bytecode is
  ignored. Tests can recreate these caches.
- Removed `logs/cn_proxy.log` from Git's index, **keeping its local contents** and
  a safety copy. Its previous tracked version remains in Git history (latest
  touching commit at review: `28f9a9a`). This is the one staged deletion; other
  changes remain unstaged/new files for review. Runtime logs are now ignored;
  evidence and historical measurement logs remain tracked.
- Consolidated README and former demo runbooks into DEMO.md. Kept compatibility
  links at both old runbook paths; preserved all screenshots, datasets, licensing,
  controller reference PDF, and historical reports. The preexisting untracked
  multi-client runbook also has an outside-repository backup.
- Replaced the flattened controller dependency freeze with direct OS-Ken and
  PyYAML requirements. Required transitive packages are still installed by pip;
  this removes redundant direct pins, not OS-Ken functionality. Matplotlib is
  documented separately as an optional plotting dependency.

No commits, pushes, merges, or publication were performed. The repository policy
was not changed. `/tmp` backups are temporary safety copies, not durable evidence.

## Historical report retained

The following report and its observations predate this review and the shared
admission changes. Its test counts and throughput claims are historical.
Use [DEMO.md](../DEMO.md) for current instructions.

# Historical test results and verification

## 1. Introduction

This document describes the functional, integration, and automated testing performed for the SDN-Enabled Concurrent TCP Proxy and Traffic Filtering System.

## 2. Network Connectivity Test

**Objective:** Verify communication between the hosts in the integrated Mininet topology.

**Test procedure:**
1. Start the integrated Mininet demo.
2. Execute the `pingall` command.
3. Observe the connectivity results.

**Expected result:** All hosts should be able to communicate.

**Observed result:** 6 out of 6 connectivity checks succeeded, with 0% packet loss.

**Status:** PASS

## 3. Proxy Communication Test

**Objective:** Verify that the client can communicate with the backend through the TCP proxy.

**Test procedure:**
1. Start the integrated topology.
2. Send a web service request from h1 to the proxy on h2.
3. Verify the response returned by the backend.

**Observed result:** The client successfully received the response:

`Web service: request received successfully.`

**Status:** PASS

## 4. Traffic Policy Tests

**Objective:** Verify that the proxy applies the configured database traffic policies.

| Policy | Test observation | Status |
|---|---|---|
| Allow | Database request was permitted | PASS |
| Deny | Database request was explicitly denied | PASS |
| Auto | Tested sensitive database response was blocked | PASS |

**Additional observation:** Runtime policy changes were tested without restarting the integrated demo.

## 5. Automated Test Results

The automated test suites produced the following results:

| Test suite | Number of tests | Result |
|---|---:|---|
| Proxy integration tests | Not separately recorded here | Passed |
| Controller wire tests | 3 | Passed |
| SDN tests | 16 | Passed |
| Combined test suite | 22 | Passed |

## 6. SDN Smoke Test

**Objective:** Verify basic SDN functionality.

**Observed result:** The smoke test completed successfully and displayed:

`SDN SMOKE TEST PASSED`

**Evidence:** `experiments/results/smoke_test.log`

## 7. Concurrent Client Testing

The benchmark program was used to evaluate requests at different concurrency levels:

- 1 concurrent client
- 5 concurrent clients
- 10 concurrent clients
- 20 concurrent clients

The direct baseline and proxy-enabled setup both achieved 100% success in the final comparison datasets.

Detailed measurements are available in `performance_evaluation.md`.

## 8. Conclusion

The tests verified network connectivity, client-to-proxy communication, traffic policy enforcement, automated test functionality, and SDN smoke-test execution. The results indicate that the integrated system performed the tested operations successfully.

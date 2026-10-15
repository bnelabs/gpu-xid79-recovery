# Independent health observation for managed GPU domains

A recovery service with an independent reset controller can still be trapped by
its detector: if `nvidia-smi` blocks, a watcher that requires NVML before dispatch
cannot reach that controller. An optional, explicitly provisioned domain health
helper removes this dependency. It does not create hardware reset authority or
replace post-recovery CUDA/fabric validation.

## Configuration

In an already qualified [domain backend](domain-adapters.md), add:

```json
{
  "domain": {
    "health_command": ["/usr/local/libexec/site-gpu-domain-health"]
  }
}
```

This fragment is not a complete backend. Keep its existing approved physical
UUIDs, platform constraints, `prefer_backend: true`, preflight/recover/validation
commands, service mapping and any prepared capsule. All members still need the
same backend configuration and must be watched. Without `health_command`, the
existing NVML health path is unchanged. A relative, empty or malformed command
is refused. Executables must satisfy the existing root-managed trust checks.

No production site helper is supplied. It must be read-only and independently
establish health for the exact installed domain through documented platform
interfaces. A BMC reporting power-on or a readable switch does not establish
GPU/driver/workload health. If observations cannot distinguish an actionable
fault from unknown or unsafe state, return `hold` or fail. A device-side RM ioctl
using the same failed driver transport is not an independent observation path.

## Request and receipt

Each invocation has a ten-second command bound and receives:

- `GPU_RECOVERY_PHASE=health` and `GPU_RECOVERY_INTENT=observe`.
- `GPU_BDF`: the watched member that triggered observation.
- `GPU_DOMAIN_MEMBERS`: approved BDF-to-physical-UUID mapping as JSON.
- `GPU_RECOVERY_BOOT_ID` and a fresh `GPU_RECOVERY_NONCE`.

The helper must independently confirm the requested mapping. It returns one
JSON object, using this illustrative shape:

```json
{
  "schema": 1,
  "nonce": "EXACT-REQUEST-NONCE",
  "boot_id": "EXACT-REQUEST-BOOT-ID",
  "members": {
    "0000:41:00.0": "GPU-11111111-2222-3333-4444-555555555555"
  },
  "independent_health": true,
  "states": {
    "0000:41:00.0": "recoverable_fault"
  }
}
```

Every approved member needs exactly one state. Unknown/extra/missing members,
stale nonce, wrong boot, mismatched physical identity or non-Boolean independence
are refused. The host boot is checked again after receipt. Environment values
are requests to verify, not observations to echo. There is no common physical
telemetry ABI inferred from this JSON protocol.

`healthy` means the qualified helper found no recovery-triggering fault in the
required host/device lifecycle checks. It does not certify a successful CUDA
workload or release an existing service hold. `recoverable_fault` recommends one
budgeted attempt; it does not guarantee that the hardware can recover. `hold`
means the state is unresolved or recovery is not currently admissible.

Any `hold` in the domain takes precedence over every fault recommendation. A
recoverable fault in any member routes through the entire approved domain, even
if another member triggered the poll. Healthy observations skip NVML and leave
normal Xid event handling in place. Stale post-recovery events remain subject to
the existing monotonic event gate.

Timeout, helper error, invalid receipt or unknown state holds all configured
domain workloads and suppresses reset for the watcher process, like existing
blocked-health handling. A later healthy observation does not release services
or silently clear that failure latch. Independently reconcile and use verified
`resume`; restart the watcher when appropriate. Hourly budgets persist as before.

## Recovery remains separately authorized

The watcher holds all domain workloads and charges every member's existing
budget before invoking recovery. The domain controller then checks its explicit
platform mapping, current/captured topology, ownership, client drain and fresh
preflight receipt. The reset backend retains its own persistent ambiguity gate.
An absent device still needs a valid prepared capsule for scoped rediscovery.

After the action, every returned GPU must pass the existing physical identity
and workload verification, followed by platform/fabric validation. A management
health receipt never replaces those tests. If the host driver remains wedged,
validation fails and services remain held.

## Evidence and limits

Unit tests cover missing peers, unknown states, strict identity/nonce/boot checks,
changed host boot, non-independent or malformed receipts, helper errors/timeouts,
command trust, whole-domain budget charging and unchanged NVML fallback.

The Switchtec QEMU campaign adds three cases on each of x86-64 and ARM64:
independent fault detection with an in-band identity probe that raises if called;
a mixed recoverable/hold domain; and a stale observation nonce. Successful fault
detection reaches the unchanged production controller and switch adapter, resets
four modeled port groups, verifies eight synthetic endpoint counters and releases
all nine modeled services. The refused observations issue no reset and retain all
holds. Existing normal, missing-device, reset-ambiguity and validation cases remain.

This is software/control-plane evidence. The fixture supplies synthetic independent
telemetry, ownership and fabric receipts; no NVIDIA or switch firmware executes.
Actual health discrimination and electrical reset on each consumer/enterprise
platform remain qualification work. The [hardware survey](enterprise-hardware-landscape.md)
and [independent-controller contract](independent-recovery-controller.md) define
those missing physical requirements.

Current acceptance: 180 unit tests passed; the six refreshed QEMU reports contain
74 passing cases (23 Redfish-domain, 13 Switchtec-domain and one storage-scope
refusal on each architecture). Report input hashes match the accepted sources.

# Optional Redfish GPU reset adapter

`adapters/redfish_gpu.py` is a platform-management transport for the existing
local-domain contract. It can discover GPU processor resources, check provisioned
serial identities and capabilities, and request their advertised restart actions.
It is installed as an optional executable; it is never automatically selected or
run by the installer. Physical BMC/GPU recovery remains unvalidated.

The adapter cannot supply a platform's missing physical reset capability. A BMC
that only exposes whole-system reset, OEM reset actions or firmware updates is
unsupported. Shared-fabric, MIG, vGPU, display and scheduler lifecycle handling
must be supplied by a platform helper. No helper for a real platform ships here.

## Provision and inspect

Copy [the example](../examples/redfish-adapter.json) to a root-managed
`/etc/gpu-recovery/redfish.json`. Replace the endpoint, CA, token and lifecycle
helper paths. The session token should be a mode-0600 file, not a command argument.
Use HTTPS with the platform's CA or system trust store; there is no insecure TLS
mode. A session token is required for real endpoints. Token issuance and renewal
remain deployment responsibilities. No credentials are stored in the example.

```bash
sudo python3 /usr/local/lib/gpu-recovery-generic/adapters/redfish_gpu.py \
  --config /etc/gpu-recovery/redfish.json --discover
```

Discovery follows the standard Systems and Processors collections, including
bounded pagination, and reports GPU processors and their advertised actions.
It sends only GET requests. It cannot establish a GPU's Linux UUID from a BMC
resource ID or prove which devices a reset physically affects. Serial-to-UUID
mapping must be provisioned from independently verified inventory; never derive
it from index order, display names or PCI bus numbers alone.

After provisioning `targets`, `--inventory` checks every mapped resource's
`@odata.id`, `ProcessorType=GPU`, serial and reset capability without requesting
a reset. Inline allowable values and ActionInfo must agree when both are
present. Only canonical directly mapped
`/redfish/v1/Systems/SYSTEM/Processors/GPU` paths and their exact
`/Actions/Processor.Reset` action are supported. This intentionally covers a
subset of Redfish implementations; OEM layouts need separately reviewed adapters.

## Connect it to the controller

For each approved member, configure the same explicit domain backend. For a
single-GPU example, place this object under its `backends` key:

```json
{
  "command": ["/usr/bin/python3", "/usr/local/lib/gpu-recovery-generic/adapters/redfish_gpu.py", "--config", "/etc/gpu-recovery/redfish.json"],
  "prefer_backend": true,
  "match": {"product_name": "REPLACE_WITH_VERIFIED_PLATFORM"},
  "domain": {
    "members": {"0000:41:00.0": "GPU-11111111-2222-3333-4444-555555555555"},
    "preflight_command": ["/usr/bin/python3", "/usr/local/lib/gpu-recovery-generic/adapters/redfish_gpu.py", "--config", "/etc/gpu-recovery/redfish.json"],
    "validation_command": ["/usr/bin/python3", "/usr/local/lib/gpu-recovery-generic/adapters/redfish_gpu.py", "--config", "/etc/gpu-recovery/redfish.json"]
  }
}
```

These are placeholders. Set `watch_gpus`, configured workload services and
physical UUIDs as described in [domain adapters](domain-adapters.md). For a larger
domain, add all members to both the adapter's targets and controller's domain;
all watched member backends must agree. Existing PCI scope checks still reject
unapproved devices and nested bridges. Absent members require the optional
[prepared-domain contract](prepared-domain-recovery.md); BMC connectivity alone
does not bypass identity or reset-scope requirements. No node/tray power cycle is attempted.

## Required platform lifecycle helper

The adapter invokes `lifecycle_command` with the existing domain environment and
`GPU_RECOVERY_PHASE=preflight` or `validation`. `GPU_REDFISH_TARGETS` additionally
contains the observed serial/resource/action mapping. The helper must independently
verify that mapping against its platform inventory and establish:

- Exclusive local authority over the complete physical reset domain, including
  dependencies not visible in the host's PCI tree.
- Drained compute/display/monitoring clients, scheduler leases, guest ownership
  and all relevant tenant state. Stopped configured systemd units alone are
  insufficient if other clients exist.
- Required driver detach/rebind or other platform reset preparation. This adapter
  does not implement those operations for an unknown platform.
- During validation, restored driver/partition/fabric state and required workloads.
  The controller also performs its configured per-member workload checks.

Return the exact nonce-bound [domain receipt](domain-adapters.md#adapter-protocol).
The helper is checked again immediately before reset requests. An echo-only helper
is not a valid implementation. The QEMU fixture helper is deliberately guest-only
and cannot qualify any real platform.

## Failure and task behavior

Only `ForceRestart` or `GracefulRestart` is admitted, and only if advertised by
that GPU's BMC resource. Requests run sequentially over the whole declared domain.
Earlier members may have reset when a later action fails. The controller keeps
all workload holds after any failure; no generic fallback follows.

HTTP 200/204 is treated as a completed management action. HTTP 202 requires a
same-origin TaskService monitor and bounded polling. A completed Task must have
`TaskStatus=OK`; failed, unknown or timed-out tasks stop the operation. A completed
management action still requires the controller's workload and fabric validation.

No POST is retried, including after connection loss. An ambiguous result is durably latched and requires
platform-specific reconciliation before another reset attempt. Redirects, remote
monitor URLs and proxy environment variables are not followed. A local 45-second
invocation deadline bounds queries, helpers and tasks below the outer controller
preflight/validation timeout. Individual HTTP and task timeout values are also
bounded. Large/slow domains may therefore refuse and need a specialized backend.

HTTP without TLS is available only for numeric-loopback lab endpoints and only
when the CLI detects the disposable QEMU guest kernel marker. This is not a real
BMC deployment mode.

## Evidence

The eight-endpoint [enterprise QEMU campaign](enterprise-emulation.md) executes
this production adapter via real subprocesses and a loopback BMC model. It tests
protocol, reset isolation, service holds and failure handling. It does not boot
NVIDIA firmware or prove a physical GPU's reset response.

Primary references:

- [DMTF Processor schema](https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/json-schema/Processor.v1_24_0.json)
- [DMTF ActionInfo schema](https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/json-schema/ActionInfo.v1_4_2.json)
- [NVIDIA PCIe card management capabilities](https://github.com/NVIDIA/nvbmc-docs/blob/develop/nvidia/Platforms/gpu-pcie-card-mgmt.md)

## Persistent reset intent and reconciliation

The adapter keeps a root-managed operation journal under
`/var/lib/gpu-recovery-generic/redfish` (override with an absolute `state_dir`).
A nonblocking lock serializes local adapter access. Atomic replacements, file
fsync and directory fsync persist the complete approved mapping before the first
POST, then checkpoint the active resource and completed actions. No credentials
are journaled. An unresolved record survives adapter/watcher restarts and host
reboots; it is not discarded when the Linux boot ID changes.

Another recovery that overlaps the same BMC origin and GPU resource is refused.
Reducing the configured domain or changing its physical identity cannot bypass
that refusal: the full original mapping and PCI scope must be restored before
reconciliation. A different BMC hostname/alias is a different origin, so keep
provisioned endpoints stable. Local journaling does not establish cross-host
exclusivity; that remains a lifecycle-helper responsibility.

After a platform-specific procedure restores the GPU domain and establishes that
no BMC task or asserted reset remains pending, use the existing controller resume:

```bash
sudo gpu-recovery resume --gpu 0000:41:00.0 --config /etc/gpu-recovery/config.json
```

The domain controller passes `GPU_RECOVERY_INTENT=resume`, performs fresh
preflight and all member workload checks, then invokes final lifecycle validation.
The helper receives `GPU_PENDING_RESETS` containing relevant unresolved journal
records. Besides its usual receipt fields, final validation must return the
Boolean `management_reset_resolved: true` when reconciling a journaled operation.
It must establish that independently from the actual platform/controller state;
echoing a field or checking GPU health alone is insufficient when a deferred reset
could still be pending. A false/missing field or failed fabric/workload check
retains the journal and holds. No reset POST is issued by resume.

A successful ordinary recovery clears its journal only after whole-domain
validation. An adapter killed before that point leaves an unresolved operation,
even if some or all reset requests completed. This conservative behavior avoids
replaying destructive operations merely because a response or controller process
was lost. Do not delete journal files as a substitute for reconciliation.

The journal unit tests use real files and locks with substituted ownership checks;
the QEMU campaign executes real root-path checks and adapter subprocesses. It
models process restart, not a physical power failure or storage durability fault.

For prepared missing-device recovery, helpers also receive
`GPU_MISSING_FUNCTIONS`. Their preflight receipt must include the exact
`missing_functions` list and `independent_reset_control: true`, established from
the actual management mapping and surviving transport. The adapter enforces
those fields before any reset request; core capsule checks then constrain
rediscovery to recorded child buses.

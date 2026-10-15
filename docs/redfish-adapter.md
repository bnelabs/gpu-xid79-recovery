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
unapproved devices, nested bridges and absent members. BMC connectivity does not
bypass those limitations. No node/tray power cycle is attempted.

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

No POST is retried, including after connection loss. An ambiguous result requires
platform-specific reconciliation before a later manual attempt. Redirects, remote
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

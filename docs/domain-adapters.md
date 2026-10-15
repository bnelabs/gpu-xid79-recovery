# Local GPU-domain adapters

An enterprise reset can involve several GPUs and a shared fabric. The controller
now supports an explicitly configured local GPU domain. A trusted adapter owns
the hardware and tenant lifecycle; the controller holds services, checks the
approved scope, runs the adapter and verifies the whole domain before releasing
any service holds. Single-GPU consumer adapters keep their existing interface.

This is an orchestration interface. The optional [Redfish adapter](redfish-adapter.md)
now supplies a BMC transport, but still needs a platform lifecycle helper. No
NVSwitch or hypervisor lifecycle implementation is supplied, and no physical
enterprise recovery has been demonstrated.

## Configuration

A backend can add a `domain` object:

```json
{
  "command": ["/usr/local/lib/platform-recovery/adapter", "recover"],
  "match": {"product_name": "REPLACE_WITH_VERIFIED_PLATFORM"},
  "prefer_backend": true,
  "domain": {
    "members": {
      "0000:41:00.0": "GPU-11111111-2222-3333-4444-555555555555",
      "0000:81:00.0": "GPU-66666666-7777-8888-9999-aaaaaaaaaaaa"
    },
    "preflight_command": ["/usr/local/lib/platform-recovery/adapter", "preflight"],
    "validation_command": ["/usr/local/lib/platform-recovery/adapter", "validate"]
  }
}
```

These paths, IDs and platform name are placeholders. No adapter at this path is
included. Put the same backend object under every member's entry in `backends`
and include every member in `watch_gpus`. The watcher refuses incomplete or
inconsistent domain configuration. Domains require explicit configuration;
automatically selected platform profiles cannot expand the recovery scope.

All executables and configuration must be root-managed. Interpreters and tools
named by those commands must also use trusted scripts, inputs and credentials.
The controller checks the first executable path; that does not validate every
argument or the implementation behind it.

## Controller checks

Before stopping workloads, the controller requires each member to be a currently
discovered NVIDIA GPU with a PCI parent. It builds the union of their slot
functions. Each immediate parent's descendants must be inside that union.
Unapproved storage, bridges and other devices stop the attempt. NVIDIA GPU,
audio, USB/UCSI and compute-accelerator function classes are accepted; other
auxiliary functions require further implementation review.

The controller snapshots PCI identities and resolved paths, holds configured
services for all members, then runs preflight. It rechecks topology before the
reset, after it and after validation. It verifies every member against the
provisioned UUID and runs each configured/default workload check. The final
adapter validator must also confirm the required fabric/partition state.
Services are released only after all these checks pass.

Without a prepared capsule, a member that has disappeared from sysfs remains
unsupported. The optional [prepared-domain path](prepared-domain-recovery.md)
adds boot-bound cached scope, fresh independent-control confirmation and child-bus
rediscovery for absent functions. It also cannot infer hidden board
wiring or devices owned by another host.

## Adapter protocol

Each stage receives these environment variables:

| Variable | Meaning |
| --- | --- |
| `GPU_RECOVERY_PHASE` | `preflight`, `recover` or `validation` |
| `GPU_BDF` | Member whose fault triggered this operation |
| `GPU_DOMAIN_MEMBERS` | JSON mapping of approved PCI addresses to physical UUIDs |
| `GPU_DOMAIN_SCOPE` | JSON array of the current approved slot functions |
| `GPU_RECOVERY_BOOT_ID` | Current Linux boot ID |
| `GPU_RECOVERY_NONCE` | Fresh unpredictable token for this operation |
| `GPU_RECOVERY_INTENT` | `recover` for a reset attempt, or `resume` for verification without resetting |
| `GPU_MISSING_FUNCTIONS` | JSON array of initially missing captured slot functions; empty for ordinary recovery |

Preflight and validation must return a single JSON object, without additional
output on stdout or stderr. The current command runner merges both streams.
Nonzero exit status, timeout, malformed JSON or a mismatched receipt stops the
operation. Preflight must inspect and quiesce the lifecycle without resetting
hardware. The recover stage performs the mapped action and returns nonzero on
failure. No generic reset fallback follows a failed domain adapter.

A preflight receipt must contain:

```json
{
  "schema": 1,
  "nonce": "COPY_CURRENT_OPERATION_NONCE",
  "boot_id": "COPY_CURRENT_BOOT_ID",
  "members": {"APPROVED_PCI_ADDRESS": "VERIFIED_PHYSICAL_GPU_UUID"},
  "scope_bdfs": ["ALL_VERIFIED_RESET_AFFECTED_PCI_FUNCTIONS"],
  "exclusive_local_control": true,
  "host_reboot_required": false,
  "clients_quiesced": true,
  "ready": true
}
```

Members and scope must exactly match the approved mapping and sorted scope, not
these placeholders. Boolean strings such as `"true"` are rejected. Validation
returns the same fields plus `workloads_verified: true` and
`fabric_verified: true`. The latter means the required fabric/partition is ready,
or the adapter has established that no such dependency exists.

Echoing environment variables is insufficient. The adapter must independently
establish its actual target mapping, reset domain, exclusive ownership and client
state. The controller can compare those claims with its configuration and local
PCI inventory; it cannot prove them from the receipt. A nonce prevents accidental
reuse of an old response but is not attestation or a sandbox for an adapter.

MIG/vGPU, hypervisor attachment, BMC authorization, fabric partition membership
and out-of-band transport survival remain adapter responsibilities. A multi-host
or rack-wide action cannot claim exclusive local control. A required host/tray
reboot fails this no-host-reboot contract.

## Watcher, budgets and resume

An attempt is charged to every domain member's hourly budget. A failed domain
attempt suppresses all its members. A completed attempt marks the recovery end
for every member, preventing an old queued peer Xid from starting another reset.
Configured per-GPU and shared-service holds use the existing persistent ledger.

`gpu-recovery resume` on a domain member runs preflight, per-GPU checks and final
adapter validation for the whole domain without running the recover command.
It cannot release just one member's holds while skipping domain validation.

## Validation limits

The suite has 106 passing tests. New cases cover stale nonces, wrong membership,
scope expansion, shared storage, platform mismatch, topology changes, partial
GPU failure, failed fabric checks, cross-member budgets and domain resume. Two
tests run actual Python adapter subprocesses. PCI inventory is a temporary
fixture; service commands, privilege checks and workload verification are
substituted. They establish protocol and orchestration behavior, not hardware
recovery or the truth of a real management controller's reports.

## Guest-kernel domain tests

The QEMU lab now has a `--domain` mode, which can combine with `--shared`. It
runs the unchanged production controller and the domain module against two PCI
test devices, with a lab-only adapter using real guest `reset_subordinate` files.

```bash
python3 experiments/qemu/run.py --domain --shared --kernel /path/to/vmlinuz \
  --output /tmp/domain-shared.json
```

The x86-64 shared-bridge test and ARM64 separate-bridge test passed: both devices'
counters changed from 1 to 0, both members were verified, and boot IDs stayed
unchanged. The guest substitutes NVIDIA vendor/class and platform identities,
workload verification uses counters, no service units are configured, and no
actual tenant or fabric state exists. These are kernel/protocol integration
tests, not NVIDIA or enterprise hardware recovery tests. The adapter refuses to
run outside an explicitly marked guest containing the exact synthetic devices.

Initial guest runs correctly failed root-managed path checks because generated
initramfs directories inherited a group-writable umask. The lab now normalizes
its executable directories and includes the root directory's permissions in
the archive. The controller's trust policy was retained.

An optional read-only `domain.health_command` provides the separate
[independent watcher health protocol](independent-health.md). Its observation
receipt is not a preflight receipt and does not grant reset authority.

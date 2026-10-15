# Prepare recovery before the GPU disappears

An optional recovery capsule lets an explicit local-domain adapter recover when
some or all GPU functions have disappeared from PCI enumeration. It records
identity and topology before the fault, then uses surviving PCI parents and a
fresh independent-control preflight during recovery. Generic recovery without
a capsule still refuses absent targets.

This closes a controller limitation. It does not create a reset signal, establish
physical wiring, or prove electrical recovery. The BMC/platform adapter must have
a reset path independent of the failed GPU link and a real lifecycle helper.

## Configure and prepare

Add `capsule` under the same explicit backend's `domain` object on every member:

```json
{
  "domain": {
    "members": {"0000:41:00.0": "GPU-11111111-2222-3333-4444-555555555555"},
    "capsule": "/var/lib/gpu-recovery-generic/domain-41.json",
    "capsule_max_age": 86400,
    "preflight_command": ["/usr/local/lib/platform-recovery/adapter", "preflight"],
    "validation_command": ["/usr/local/lib/platform-recovery/adapter", "validate"]
  }
}
```

This is a partial example; retain the backend command, `prefer_backend: true`
and platform identity constraints from [the domain contract](domain-adapters.md).
Provision `expected_gpu_uuids` for every member, the watched GPU allowlist and
all relevant workload services. A Redfish deployment uses the command arrays in
[the Redfish guide](redfish-adapter.md) instead of these placeholders.

While all GPUs are present and reporting their provisioned identities:

```bash
sudo gpu-recovery prepare --gpu 0000:41:00.0 --config /etc/gpu-recovery/config.json
```

Preparation queries each member's UUID/PCI identity through NVIDIA tools and
reads PCI topology. It writes a root-managed capsule and takes the controller's
nonblocking orchestration lock. It does not reset hardware, stop workloads, run
CUDA, discover motherboard wiring or certify complete GPU health. The record
contains no cached BAR values or arbitrary PCI configuration bytes.

When a capsule is configured, the watcher refreshes it after successful identity
polls, at most once every `min(300, capsule_max_age / 2)` seconds per capsule.
All members must pass preparation; a partially captured record is never
installed. A busy controller skips refresh. Ordinary preparation failures are
logged; an I/O failure, blocked query or wrong physical UUID retains the existing
fault/hold behavior. Preparation is not a reset attempt and does not consume reset budget.

The default age bound is 24 hours, configurable from 60 seconds to seven days.
Boot identity, kernel release, NVIDIA module presence/version, board/BIOS fields,
and the configured backend object must match. Reboot, changed configuration or
expiry requires fresh preparation while the domain is present. These fields are
compatibility checks, not firmware attestation or hashes of adapter executables
and their external configuration files.

## Recovery sequence

An explicitly selected missing member can use the existing `recover`/`auto`
command or the watcher. The watcher avoids querying NVML for a prepared GPU
already absent from sysfs, then applies the same whole-domain holds and budgets.
Direct invocation holds configured capsule-domain services even if the record
later fails validation.

The controller checks every surviving recorded parent by PCI identity, resolved
physical path and unique child-bus rescan path. All visible descendants must be
inside the captured scope. Visible member functions must retain their recorded
identities and paths. Unexpected storage, bridges, peers, an extra cached rescan
bridge or a changed upstream device stop the attempt before reset.

Preflight receives the complete approved member/slot scope plus:

```text
GPU_MISSING_FUNCTIONS=["0000:41:00.0"]
```

When that list is nonempty, its normal nonce-bound receipt must additionally
contain the exact same `missing_functions` array and the Boolean
`independent_reset_control: true`. The adapter/helper must independently establish
its management target, ownership and full physical reset scope despite the absent
host functions. Merely repeating the environment is insufficient. The Redfish
adapter enforces those claims in its own helper calls as well.

The controller rechecks presence and scope after preflight, then invokes the
independent adapter. Only after it returns success does the controller rescan
each recorded bridge's **child-bus** interface, never the global PCI rescan or
old GPU BAR addresses. Rescan writes use the existing bounded subprocess runner.
The complete captured function set must return. Fresh driver-visible GPU UUIDs
and workloads are checked for every member; final fabric/partition validation
must pass before service holds are released.

Explicit domain recovery now goes directly through its own platform/scope
checks, avoiding an unnecessary `explore`/NVIDIA diagnostic query before the
management adapter. This does not permit the watcher to bypass a timed-out
health operation on a still-enumerated GPU: a potentially stuck kernel operation
continues to stop further automatic reset work.

## Boundaries

- A snapshot of IDs/BDFs alone does not establish unique physical identity.
  Provisioned UUIDs, independently verified management mapping, and fresh
  post-return checks are all required.
- A missing/changed upstream bridge, nested switch hierarchy, renumbered BDFs,
  changed BIOS/kernel/module state or unapproved scope is unsupported. No global
  rescan, guessed GPIO, driver-module unload or peer expansion is added.
- A rescan may allocate resources and trigger driver probes. It cannot restore
  power, release an asserted reset, clear DPC correctly on its own, or fix a
  physically broken link. The lifecycle helper must coordinate kernel hotplug,
  driver and management ownership. NVIDIA module presence/version must remain
  compatible with the prepared record.
- `resume` requires all recorded functions to be present already. It never uses
  a capsule to issue a reset or rescan. Persistent ambiguous-reset journals still
  require the complete management reconciliation documented in the Redfish guide.
- Host reboot invalidates the capsule even though an unresolved management reset
  journal survives reboot. A capsule is not permission for a blind cold-start
  reset from an earlier boot.

## Evidence

The enterprise QEMU campaign logically removes two endpoints, then all eight
endpoints in separate cases, while retaining independently mapped guest BMC
controls. It runs production capsule capture, domain orchestration, Redfish
subprocesses and actual child-bus rescans. Freshly mapped testdev counters replace
GPU workload verification. It also tests missing independent-control confirmation,
expiry, changed recorded parent and a substituted wrong UUID after rediscovery.

Unit cases additionally check new peers after preflight, stale boot/backend,
unsupported cached function classes, unrelated rescan bridges, invalid PCI paths,
refresh rate limiting, verification-only resume and strict receipt Booleans.
This evidence establishes controller/kernel behavior in these fixtures, not
physical NVIDIA firmware recovery from an electrically failed link.

Primary references:

- [Linux PCI sysfs ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-bus-pci)
- [Linux PCI recovery requirements](https://docs.kernel.org/PCI/pci-error-recovery.html)
- [NVIDIA Xid analysis and recovery actions](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)

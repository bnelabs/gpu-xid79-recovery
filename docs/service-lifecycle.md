# Service handling across consumer and enterprise setups

The generic controller now restores configured workloads only after recovery
verification succeeds. Failure leaves them stopped. The behavior uses configured
systemd units and PCI addresses; it contains no application, hostname, board or
GPU-model-specific service names.

## Map workloads to GPUs

`services` applies to every watched GPU. `gpu_services` adds services for one PCI
function. Entries support system units or a `user` plus unit name. Duplicate
entries are merged. See [single-GPU example](../examples/consumer-inference.json)
and [multi-GPU example](../examples/multi-gpu-services.json). Replace all example
addresses, UUIDs and unit names before use.

A shared service has one hold record with a list of blocking GPUs. If GPUs A and
B fail, successful recovery of A removes only A's blocker. The service starts
only after B also passes verification. This coordinates service restoration; it
does not authorize a reset affecting either GPU's peers.

Desired service state is recorded before stopping a unit, in the root-managed
`/var/lib/gpu-recovery-generic/services.json`. It survives a failed attempt and a
controller restart. Inactive units that were not previously held are not started.
An activating/reloading unit is also held. Failed service startup retains its
record so a later verified resume can retry it. Configuration changes do not
automatically restore units removed from the configuration.

## Hold, check and resume

```bash
sudo gpu-recovery hold --gpu 0000:41:00.0 --config /etc/gpu-recovery/config.json
gpu-recovery check --gpu 0000:41:00.0 --config /etc/gpu-recovery/config.json
sudo gpu-recovery resume --gpu 0000:41:00.0 --config /etc/gpu-recovery/config.json
```

`hold` stops configured services without a reset and works when the GPU has
vanished. `check` verifies current identity and runs the configured workload test;
by default this is a fresh CUDA context, transfers and a small kernel. It does
not change services or PCI state. `resume` repeats verification and then releases
that GPU's service holds without resetting it. It preserves validation from a
matching platform profile, including a configured fabric-specific validator.

Applications can use `check` as a systemd `ExecStartPre` gate. They must also
require their GPU backend in their own configuration. A startup check alone
cannot stop an application from choosing CPU later. Keep `cuda_validation` enabled
or provide an appropriate workload validator; explicit NVML-only validation has
weaker guarantees.

## Watcher behavior

The watcher follows new Xid79 events and polls each allowed GPU's NVML identity
at startup and every `watch_health_interval` seconds (default 15; range 5..3600).
It holds configured services before trying recovery, including when the device
has disappeared or attempts are suppressed. A command timeout or an unexpected
GPU identity holds services and prevents a reset attempt.

Set `expected_gpu_uuids` to bind PCI addresses to known physical GPUs. Otherwise
the first successful query pins the UUID for the current boot, and that pin
survives watcher restarts. This first-seen identity is weaker than administrator
provisioning and is not a persistent identity claim across boots.

A root-managed budget records at most three recovery invocations per GPU per
hour and survives watcher restarts within the same boot. A failed recovery also
suppresses that GPU for the rest of the watcher process's lifetime. Restarting
it clears the failure latch but not the hourly budget. Other GPUs retain separate
budgets. Old queued events do not reset a currently healthy GPU again after its
completed recovery. One watcher manages the host's allowlist; avoid overlapping
recovery controllers.

Generic recovery does not automatically repeat failed hardware resets. An
adapter may implement a separately tested, bounded retry sequence for its own
platform. A blocked kernel call still stops recovery.

## Enterprise boundaries

Per-GPU services and shared-service holds support headless consumer machines,
workstations and multi-GPU orchestration. They do not establish tenant ownership,
MIG instance recreation, hypervisor authority, NVLink partition health or BMC
reset wiring. Those remain platform-adapter responsibilities. Generic kernel
fallback continues to refuse multi-GPU, NVLink, MIG and virtualized cases that
need such lifecycle handling. No peer-reset expansion or global module unload
was added.

Default CUDA validation checks one physical GPU. Enterprise deployments should
supply a validator covering their required instance/fabric/workload state. A
consumer model name, enterprise SKU or an empty blocker list is not proof that
recovery will work.

## Validation

83 tests passed, including service-state persistence, two-GPU shared-service
blocking, failed workload verification, UUID changes, blocked health commands,
restart-persistent budgets and preservation of platform-specific validation.
Service commands are mocked in those unit tests; ledger files are real temporary
files. A separate real systemd user-unit hold/resume test passed with fresh CUDA
validation and no GPU reset.

The unchanged generic reset path also passed an x86-64 QEMU isolated-target test
and an ARM64 shared-bridge refusal test. These use synthetic endpoints and do not
validate NVIDIA firmware or enterprise physical recovery.

Explicit adapters can now coordinate a [local GPU domain](domain-adapters.md), with whole-domain validation, holds and budgets. Hardware and tenant lifecycle implementation remains adapter-specific.

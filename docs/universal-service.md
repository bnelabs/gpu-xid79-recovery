# A recovery service across GPU platforms

The feasible common interface is a capability-based recovery service. There is
no demonstrated single reset command that restores every consumer and data-center
GPU after Xid79. NVIDIA itself states that GPU reset is not guaranteed and that
post-reset health must be verified. A service must report an unrecoverable device
and retain workload holds when its supported paths are exhausted.

## Assess a host without recovering it

```bash
python3 gpu_recovery.py assess --config examples/config.json
# Installed deployment, using your provisioned configuration:
sudo gpu-recovery assess --config /etc/gpu-recovery/config.json
```

Add `--gpu 0000:41:00.0` to restrict the report. Otherwise the command includes
discovered GPUs and configured `watch_gpus`, including missing devices. It reports
UUID provisioning, workload-service configuration, validation settings, unknown
lifecycle state, and existing kernel fallback blockers. Ordinary device errors
remain in the report; a command timeout aborts further probing.

The schema-1 JSON is an inventory, not a certification or a dry run of recovery.
It does not stop services, reset hardware, test CUDA, validate adapter executables,
or enable the watcher. Like `explore`, it may execute installed profiles' trusted
read-only compatibility checks. An empty gap list never establishes support.
Unknown MIG, virtualization or NVLink state remains unknown, including after a
fault that makes NVML unavailable. The existing runtime reset guards are unchanged.

## Common controller and platform backends

| Failure or deployment | Candidate route | Qualification requirement |
| --- | --- | --- |
| Driver-responsive GPU | Targeted NVIDIA reset | Drain all relevant clients; confirm supported reset mode and affected peers |
| Isolated PCIe endpoint | Kernel device/ACPI/subordinate reset | Confirm actual reset effect and driver detach/rebind on the exact platform |
| Lost link that survives upstream reset | Independent PERST or slot-power adapter | Establish physical wiring, polarity, affected devices and transport independence |
| Multi-GPU / NVSwitch / MIG / virtualized system | Lifecycle domain adapter | Establish ownership, full affected domain, scheduler/tenant drain and partition/fabric restoration |
| GPU absent from sysfs | Prepared capsule plus independent lifecycle adapter; otherwise hold | Boot/config/topology checks, independent identity/control confirmation, scoped rediscovery and full-domain verification |
| No usable independent control or failed reset | Hold and escalate to platform operator | Node/tray power cycle or hardware repair may be required |

Existing systemd orchestration supplies persistent holds, per-GPU budgets, bounded
commands and fresh CUDA verification. The [domain protocol](domain-adapters.md)
can coordinate approved local groups. The optional [Redfish adapter](redfish-adapter.md) now supplies a scoped BMC
transport. It does not supply scheduler drain, tenant ownership or fabric reconstruction. Those must be
implemented against a specific platform's documented interfaces and validated.
No automatic node power cycle belongs in the current no-host-reboot contract.

## What the firmware research contributes

Reviewed research repository revision:
`eda08577e5653d84daa3dc07718888315bd6ab78`.

Its [rmctl notes](https://github.com/bnelabs/nvidia-gpu-firmware-research/blob/eda08577e5653d84daa3dc07718888315bd6ab78/07-prototype/rmctl-notes.md)
record a successful read-only GSP feature query on RTX 3090 / driver 615.71.09.
That supports a future diagnostic collector for driver/GSP agreement. It does
not establish reset authority, cross-version ABI compatibility, or a transport
that works after PCIe access is lost. Do not introduce arbitrary RM control
passthrough into the service. Pin any future probe to reviewed headers and
explicit read-only command IDs, select by physical identity, and bound its runtime.
The prototype is not imported or executed by this change.

Its firmware corpus and version manifests can help correlate failures with exact
software versions. Use the existing `gpu-firmware-audit` for offline image
inspection, with driver-version matching. A file hash does not prove that image
is running. Firmware replacement, unsigned images and direct BAR writes are not
a demonstrated universal recovery route. Shared GSP filenames do not establish
identical reset behavior. The research repo's unverified notes remain hypotheses.

## Required evidence before offering recovery as a service

Maintain a qualification record for each GPU PCI/subsystem identity, board/BIOS,
CPU/root-port topology, kernel, driver/GSP version and adapter version. Record
reset scope, independently controlled targets, workload drain mechanism, and
fabric/virtualization lifecycle dependencies. Define the supported deployment
explicitly: headless consumer, desktop/display, standalone enterprise PCIe, or
fabric domain. A headless success cannot qualify desktop session recovery.

On a dedicated bench, exercise responsive-driver failure, inaccessible endpoint
configuration, missing device, busy clients, blocked teardown, failed adapter,
and repeated faults. Distinguish synthetic injection from an actual physical
Xid79 event. For each attempt retain before/after topology and identity, monotonic
durations, command outcomes, boot ID, held workloads, CUDA results and platform
validation. Verify peers and storage remain usable and that failed verification
never releases services. Measure successes over attempts and recovery latency;
report workload interruption and escalation rates as well.

Start with the existing RTX 3090/MS-7D25 baseline, then qualify a second consumer
board and a standalone enterprise PCIe GPU. A separate NVSwitch/MIG deployment
needs a real lifecycle adapter and its own acceptance campaign. Cross-generation
source review and QEMU tests remain supporting evidence, not physical recovery
qualification. Physical enterprise and additional consumer hardware remain the
blocking evidence for a broad support claim.

## Primary references

- [NVIDIA reset semantics and limitations](https://docs.nvidia.com/deploy/nvidia-smi/index.html#device-modification-options)
- [NVIDIA Xid catalog](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
- [NVIDIA driver and matching firmware requirements](https://github.com/NVIDIA/open-gpu-kernel-modules#compatible-gpus)

The [prepared-domain implementation](prepared-domain-recovery.md) now removes
the previously unconditional refusal of vanished PCI functions. It prepares
identity/scope while available and uses independently mapped controls after
failure. It does not infer a missing physical reset path from a firmware image.

The [independent-controller engineering contract](independent-recovery-controller.md)
defines a motherboard-independent hardware direction, an offline qualification
checker, and the physical evidence still required.

The [enterprise hardware survey](enterprise-hardware-landscape.md) records
documented configurations and qualification gaps. Hardware names alone do not
select a reset contract, particularly for CPU-coherent and provider-owned systems.

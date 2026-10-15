# Enterprise Blackwell recovery

B200/B300 SXM and GB200/GB300 systems are in scope. No physical recovery test on these systems has been performed here. The controller discovers NVIDIA devices by vendor and PCI class, rather than maintaining a consumer-only model allowlist. Discovery is not a promise that a reset backend exists.

## Driver paths

The reviewed NVIDIA open driver is 615.78.08, commit `ca2d03e6fafd4d4b56b383497d931c25b283bfcf`. Its supported-device table includes B200 (`2901`, `2909`), GB200 (`2941`), B300 SXM6 AC (`3182`), and GB300 (`31c2`, `31c3`). Subsystem IDs distinguish additional variants; these device IDs alone cannot identify a reset domain. B100 was not listed by name in this reviewed table.

The generated HAL assigns GB100, GB102, GB110 and GB112 to `kbifTriggerFlr_GB100` and `kbifConfigAccessWait_GB100`. The trigger reads PCI device control, changes the FLR field, then writes it back. The wait polls the NVIDIA vendor ID through configuration cycles with a timeout and 1 ms delays. These operations depend on configuration access. They cannot reach an electrically disconnected device through a dead PCIe link.

Sources: [supported devices](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/README.md), [HAL dispatch](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/generated/g_kernel_bif_nvoc.c), [GB100 implementation](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/bif/arch/blackwell/kernel_bif_gb100.c).

## Firmware fixes that affect recovery

| Platform and release | Documented change | Consequence for a recovery adapter |
| --- | --- | --- |
| DGX B200 GPU tray 1.3.00 | Fixes RM initialization failures after GPU reset, and a reset corner case leaving GPUs absent from nvidia-smi across VM reboots | Record the tray firmware package as well as the driver version before diagnosing reset failures |
| DGX B200 GPU tray 1.4.00 | HMC retries telemetry when PERST is asserted again shortly after reboot | Missing management telemetry immediately after reset is not sufficient evidence that GPU initialization failed |
| DGX B200 GPU tray 1.4.30 | Fixes an NVLink firmware scheduling bug after roughly 60 days that can hang the driver and report Xid150 | Similar symptoms can have a firmware-specific cause; do not classify every hang as Xid79 |
| DGX GB300 NVL72 stack 2.0.0 | Fixes reset failures after some NVSwitch trays reboot during an NVLink SHARP workload; earlier failures could require rack AC/DC cycling | Check the matched platform stack before treating rack cycling as unavoidable for this particular failure |

The B200 entries are from [NVIDIA's firmware change history](https://docs.nvidia.com/dgx/dgxb200-fw-update-guide/firmware-changes.html). The GB300 entry is from [the 2.0.0 improvements](https://docs.nvidia.com/dgx/dgxgb300nvl72-2-release-notes/improvements.html), item 28. These are specific fixes, not a guarantee against every Xid79. The kernel version mentioned in item 27 addresses a different NVLOOM issue and must not be presented as the minimum version for item 28.

## Required platform contract

For HGX/DGX SXM systems, an adapter needs a verified mapping between GPU UUID, SXM location, management endpoint and reset domain. It also needs the actual installed GPU-tray, HMC/BMC, GPU firmware and fabric-stack versions. A missing Redfish part number cannot safely stand in for that mapping: NVIDIA's B200 history documents missing SXM part numbers on B200, B300, GB200 and GB300 platforms.

For Grace Blackwell, include CPU–GPU coherence, compute-tray ownership and fabric partition membership in that contract. Resetting a compute tray or a rack may restart hosts and therefore fails this project's no-host-reboot objective. A GPU-scoped action must establish its narrower scope before use.

An independent NSM/BMC path is promising only when its command is supported, its target mapping is verified and its transport remains available after the GPU fault. The existing [NSM review](out-of-band-reset.md) and [management firmware review](management-firmware.md) explain why a protocol codec or management-interface reset alone is insufficient.

After an action, verify physical identity, driver initialization, fresh CUDA execution and platform fabric health. A single-GPU CUDA check does not verify NVLink routing, partition membership or collectives. Generic kernel fallback continues to refuse multi-GPU, NVLink, MIG and virtualized cases that need an adapter.

## What the emulator establishes

The current NVIDIA guest lab exercises rejection of an endpoint whose BAR reads return all ones. It exits before family-specific initialization. Changing that endpoint's PCI ID to B200 or B300 would not test their firmware, coherence, NVSwitch lifecycle or recovery. No Blackwell hardware validation is claimed from that lab.

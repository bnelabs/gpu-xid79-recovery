# GPU families and firmware

Discovery accepts NVIDIA display and compute-accelerator PCI classes. A model name does not select a reset pin or prove that recovery will work.

| Hardware | Driver paths reviewed | Hardware recovery tested in this project |
| --- | --- | --- |
| RTX 30 / Ampere | GA100 full-chip path, TU102-family FLR dispatch | RTX 3090, using its platform adapter |
| RTX 4090 / Ada AD102 | GA100 full-chip path, TU102 FLR implementation, Ada GSP scrubber | No |
| RTX 5090 / Blackwell GB202 | GH100 FLR implementation, GB202 BAR firewall wait | No |
| Hopper / H100 | GH100 FLR and secondary-bus reset, fabric constraints | No |
| B200 / B300 SXM and GB200 / GB300 | GB100-family FLR trigger and config-readiness wait; platform firmware and fabric lifecycle | No |
| Other or future NVIDIA devices | Runtime discovery; capability and topology checks | No |

This review uses NVIDIA open kernel modules 615.78.08, commit `ca2d03e6fafd4d4b56b383497d931c25b283bfcf`. It reviews host-side firmware loading and reset control, not a complete decompilation of proprietary VBIOS or GSP firmware.

## What changes between Ada and Blackwell

The driver's generated HAL dispatch assigns AD102 to the TU102 FLR implementation and GB202 to the GH100 implementation. Blackwell data-center chips use a different GB100 FLR trigger. Each still needs working PCI configuration cycles to request FLR.

GB202 also waits for the BAR firewall to disengage after reset. The driver describes FSP startup and configuration retry release taking roughly 800 ms, followed by a bounded firewall wait. A readable vendor ID alone therefore does not mean that firmware and BAR access are ready. Recovery must complete driver initialization and a workload check.

The driver loads a common `gsp_ga10x` container for several families, including Ada, Hopper, and Blackwell. It selects family-specific signature sections inside that container. Sharing a filename does not imply identical boot or reset behavior. The container version must match its driver release.

For B200/B300 SXM and Grace Blackwell systems, see the [enterprise Blackwell review](enterprise-blackwell.md). It records reset-related firmware fixes and separates a GPU reset from a tray or rack power cycle.

## Reset selection

`kernel_reset_mode` controls the kernel fallback after NVIDIA reset fails:

- `auto`: try the device's kernel reset, then the bridge's `reset_subordinate` when exposed. If PCI vendor reads return all ones or zero and the bridge reset exists, use that bridge path directly.
- `device`: use the target function's `reset` file. Its enabled kernel methods determine what kind of reset occurs; this option does not force FLR.
- `acpi`: temporarily select the kernel-listed `acpi` method for the target device, use its `reset` interface, then restore the original enabled-method order. If `acpi` is not listed, stop before detach. A blocked operation stops all further calls.
- `bus`: use the upstream bridge's `reset_subordinate` directly. If the kernel does not expose it, stop before unbinding anything.

Every kernel path still requires an isolated slot. Multi-GPU, MIG, NVLink, and virtualized setups require a lifecycle adapter when NVIDIA reset fails. Active-user or OS permission rejections stop recovery.

The controller detaches the slot's auxiliary functions and GPU, performs the selected kernel reset, and rebinds the original drivers. It does not reuse an old disconnected GPU's RM object tree or write generation-specific reset registers.

A secondary-bus reset can recover some firmware hangs, but it cannot guarantee recovery of a completely lost link. Such failures may need an independently mapped PERST or power controller. Reset metadata in the GPU's firmware does not identify the motherboard's wiring.

## Inspect a GSP image

```bash
gpu-firmware-audit /lib/firmware/nvidia/DRIVER_VERSION/gsp_ga10x.bin \
  --driver-version DRIVER_VERSION
```

This prints hashes, ELF sections, the embedded version, and signature-section family names. A version mismatch exits with status 2. The command reads the specified file; it does not load it, verify its signatures cryptographically, or establish which image a running GPU loaded. Compressed files must be decompressed into a separate file first.

## Sources

- [NVIDIA HAL reset dispatch](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/generated/g_kernel_bif_nvoc.c)
- [GB202 firmware/BAR readiness](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/bif/arch/blackwell/kernel_bif_gb202.c)
- [GB100 reset and readiness](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/bif/arch/blackwell/kernel_bif_gb100.c)
- [Firmware container selection](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/kernel-open/common/inc/nv-firmware.h)
- [NVIDIA reset requirements](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [Enterprise out-of-band management](https://github.com/NVIDIA/nvbmc-docs/blob/develop/nvidia/Platforms/gpu-pcie-card-mgmt.md)

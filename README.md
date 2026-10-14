# Capability-based NVIDIA GPU recovery

A motherboard-independent Linux controller for discovering NVIDIA GPUs, inspecting their PCIe reset scope, and attempting recovery after Xid79 without rebooting the host.

**The core contains no board names, GPIO numbers, PCI addresses, driver instruction offsets, or GPU model whitelist.** It identifies NVIDIA VGA/3D GPUs and processing accelerators by PCI vendor/class and reads their advertised capabilities. GeForce, RTX/Quadro workstation, Tesla, and NVIDIA data-center/industrial GPUs are in scope, with no generation or model whitelist. NVIDIA networking, audio, and NVSwitch devices are not treated as GPU targets. Standard reset selection therefore does not depend on a 3090, or on a 30/40/50/60-series marketing name. Installed driver support and real hardware reset capabilities still matter; future models and other generations have not been hardware-validated here.

Every recovery attempt explores the board first, then acts on the resulting capability report. Independently verified platform profiles can supply PERST or power control when standard interfaces are insufficient.

Motherboard independence does not mean every GPU failure is recoverable in software. An electrically disconnected GPU may need a reset/power mechanism its motherboard does not expose. The controller reports that failure instead of inventing a successful recovery or rebooting the server.

## Install and inspect

Python 3.10+, Linux PCI sysfs, NVIDIA driver tools, and systemd are required. No Python packages are needed.

```bash
git clone git@github.com:bnelabs/gpu-xid79-recovery.git
cd gpu-xid79-recovery
sudo ./install.sh
gpu-recovery discover
sudo gpu-recovery explore --gpu 0000:07:00.0
```

The PCI address above is an example: use a discovered address. `discover`, `explore`, and `plan` do not reset hardware. Root exploration can read privileged PCI configuration space and run installed, root-managed adapters' read-only compatibility checks. Without root, matched profiles are reported as pending that check. If exactly one NVIDIA GPU or processing accelerator exists, `--gpu` can be omitted. Recovery and watching require root.

Installation does not enable a watcher or reset hardware. It installs the CLI at `/usr/local/sbin/gpu-recovery` and a default empty configuration at `/etc/gpu-recovery/config.json`.

## Standard recovery

```bash
sudo gpu-recovery recover --gpu 0000:07:00.0 --config /etc/gpu-recovery/config.json
# Equivalent: explore the host, choose a compatible recovery route, and execute it.
sudo gpu-recovery auto
```

The controller:

1. Explores DMI/BIOS identity, chipset devices, GPU PCI/subsystem identity, live configuration reachability, BAR allocations, IOMMU group, PCIe link, slot functions, current drivers, parent bridge, and kernel reset interfaces. Reads standard and extended PCI capability chains, including advertised FLR, power management, AER, ACS, DPC, and Resizable BAR where available. Inventories ACPI table identities/hashes, the GPU firmware-node association, GPIO controller labels/line counts, and IPMI device presence.
2. Matches installed platform profiles against both board and GPU identities and runs their read-only compatibility checks. Exactly one checked match may be selected automatically; ambiguous matches require explicit configuration. Explicit configured backends take precedence.
3. Refuses PCI reset or lifecycle-backend fallback if the reset scope contains another slot, storage, a bridge, or unknown devices. A targeted NVML reset can still be attempted on a shared bridge.
4. Stops the configured services that were active. It does not kill arbitrary processes.
5. Attempts NVIDIA's reset targeting only the selected PCI address unless the verified backend requests priority. It never expands the request to peer GPUs. Visible CUDA clients or an NVIDIA “in use” refusal abort recovery.
6. If needed, invokes the selected lifecycle backend. Without a backend, detaches only this GPU slot's functions, tries kernel function/subordinate reset, and rebinds the original drivers.
7. Checks NVML and runs an optional workload validator pinned to the selected GPU UUID through `CUDA_VISIBLE_DEVICES`.
8. Restores previously active services and checks that Linux boot identity is unchanged.

Low-level inventory is evidence, not a wiring diagram. ACPI metadata, a GPIO label, or an advertised PCI capability does not prove that a particular pin can safely reset a slot, that a firmware method has a local scope, or that a reset will work after link loss. The core does not guess pin assignments, execute unknown ACPI methods, flash firmware, or derive executable driver patches. Missing mechanisms are reported; additional verified profiles can extend supported hardware without changing the generic core.

A timed-out kernel command stops further reset attempts. SIGKILL does not necessarily terminate a kernel task stuck in D-state. If detach/reset is blocked, the controller does not issue another rebind/reset against that locked device. It does not globally unload NVIDIA modules, remove/rescan PCI trees, write arbitrary registers, guess GPIOs, or invoke reboot/suspend.

The generic kernel path may fail in the same NVIDIA teardown waits observed on the original server. Driver-specific kernel patches belong in a version-checked adapter, not the generic core.

## Workstation, industrial, and data-center scope

Discovery accepts NVIDIA VGA/3D classes (`03xx`) and processing accelerator class (`1200`), including headless compute GPUs. This is discovery support, not a claim that every listed product has been hardware-tested.

Exploration also queries driver-reported architecture, UUID, MIG mode, virtualization mode, fabric state, and topology. Unsupported/unavailable fields remain unknown. Targeted NVIDIA reset is still the first standard route. As documented by [NVIDIA](https://docs.nvidia.com/deploy/nvidia-smi/index.html), NVLink peers and Fabric Manager can affect reset scope. The controller never automatically expands a reset to peer GPUs or stops Fabric Manager/DCGM. If targeted reset fails, generic PCI fallback is refused on multi-GPU hosts, when NVLink is detected, when MIG is enabled, or in a reported virtualized GPU mode. A verified lifecycle adapter is required for those broader recovery cases. MIG partitions, vGPU tenants, and NVSwitch fabrics are not destroyed/reconfigured by the generic core.

No server-specific workload service is required or auto-discovered. The default `services` list is empty. Administrators may explicitly list their GPU workers to drain, using the optional configuration below. Platform adapters are external privileged code and must implement their own appropriate workload lifecycle; the historical MS-7D25 adapter is optional and specific to the original server.

## Configure workloads and validation

Edit the root-owned `/etc/gpu-recovery/config.json`:

```json
{
  "watch_gpus": ["0000:07:00.0"],
  "services": [
    {"unit": "my-gpu-worker.service"},
    {"user": "alice", "unit": "gpu-worker.service"}
  ],
  "validation_command": ["/usr/local/lib/my-gpu-tests/cuda-check"],
  "backends": {}
}
```

Only active listed services are stopped/restarted. User-service UIDs are discovered from the account database. For a GPU that cannot be queried, client absence may be unknown; configure its workload services explicitly. NVML response alone is reported as NVML health, **not as proof that CUDA works**. A workload validator must exit nonzero on failure. The validator receives `GPU_BDF` and a target-only `CUDA_VISIBLE_DEVICES` UUID.

Privileged configuration files and command executables, including their parent directories, must be root-managed and not writable by group/others. Commands are argument arrays executed without a shell. Treat adapter arguments and executables as privileged administrator code.

## Platform adapters

A lifecycle adapter owns the complete hardware reset, PCI restoration, and driver cleanup for a platform. It must target the requested GPU, verify its hardware identity and reset scope, enforce its own driver/kernel compatibility requirements, and exit nonzero on failure. Arbitrary firmware controls cannot be inferred safely from a PCI device ID.

```json
{
  "watch_gpus": ["0000:07:00.0"],
  "backends": {
    "0000:07:00.0": {
      "command": ["/usr/local/sbin/my-verified-gpu-reset"],
      "match": {"board_vendor": "Verified Vendor", "board_name": "Verified Board"},
      "requires_single_gpu": true,
      "prefer_backend": true
    }
  }
}
```

The backend receives `GPU_BDF`, `GPU_BRIDGE`, and JSON `GPU_SLOT_FUNCTIONS` environment values. The core verifies the configured DMI identity constraints before stopping services or running it. `requires_single_gpu` protects adapters that manage a global NVIDIA driver lifecycle. `prefer_backend` skips NVML when a verified platform recovery is preferable; the CLI also accepts `--backend-only` for explicit adapter testing.

The included [MS-7D25 configuration](examples/ms7d25-adapter.json) integrates the separately installed [verified board-specific implementation](https://github.com/bnelabs/ms7d25-gpu-recovery). That adapter keeps its GPIO wiring and experimental driver offsets in its own version-pinned package. They are not made universal by this controller.

The default installer adds no board-specific profile and no workload-service dependencies. An optional [verified discovery profile](profiles/ms7d25.json) can be installed with `sudo ./install.sh --profile ms7d25`; existing administrator profiles are preserved. On the original server, `sudo gpu-recovery auto` finds this adapter without a board-specific CLI argument. Other boards fall back to standard NVIDIA/kernel interfaces. This profile deliberately constrains the GPU identity and slot because its external adapter does; the generic core imposes no such model restriction. A profile needs board and PCI identity constraints, a root-managed backend executable, and a read-only `check_command`. The check must validate any wiring, topology, kernel, driver, and resource requirements before returning success. A profile's optional `validation_command` supplies CUDA verification when selected.

## Automatic operation

After configuring an explicit `watch_gpus` allowlist:

```bash
sudo systemctl enable --now gpu-recovery-generic.service
sudo journalctl -u gpu-recovery-generic.service -f
```

The watcher reads new kernel Xid79 events, resolves their GPU PCI address, and applies the same recovery policy. Each GPU is limited to three attempts per hour. A failure suppresses further attempts for that GPU until watcher restart. Unknown GPUs and other Xids are ignored.

**Run only one recovery watcher for a GPU.** The generic watcher is installed disabled. It depends only on the kernel journal and your explicit GPU allowlist; it has no llama, Harness, or other application-service dependency.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

Tests cover nonhardcoded PCI addresses, shared-bridge storage refusal, blocked detach stopping subsequent operations, original-driver rebind order, command deadlines, Xid targeting, root-managed command checks, and CUDA UUID isolation. Synthetic PCI/sysfs tests are not hardware-recovery claims.

On the original server the generic controller completed a real, full hardware reset/reload cycle through its verified MS-7D25 lifecycle adapter, then passed its own NVML and CUDA validator. See [live-adapter-test.log](live-adapter-test.log). Linux boot ID stayed unchanged. This test was performed on the recovered, healthy GPU; it did not inject another Xid79.

The standard NVML and generic sysfs recovery paths have not been hardware-validated on other motherboards. No universal recovery guarantee is claimed. The original real-Xid79 recovery evidence is in the board-specific repository.

The updated automatic flow was tested on this server at 20:41 on 2026-10-14. Exploration found live GPU config space returning `ffff` and NVML unable to find the device. It automatically selected and checked the installed adapter, restored the GPU, and passed CUDA transfer correctness checks with unchanged boot ID. See [live-auto-test.log](live-auto-test.log), [board-exploration.json](board-exploration.json) for the failed-state inventory, and [board-exploration-after.json](board-exploration-after.json) for the recovered capabilities. This failure was observed directly; no new Xid79 was recorded in the preceding ten minutes, so it is not labeled as a newly observed Xid79 event. The original watcher was restored afterwards.

## References

- [NVIDIA reset capabilities and limits](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [Linux PCI reset/sysfs ABI](https://docs.kernel.org/6.14/admin-guide/abi-testing.html)
- [Linux PCI error recovery](https://docs.kernel.org/PCI/pci-error-recovery.html)
- [MS-7D25 actual recovery and driver investigation](https://github.com/bnelabs/ms7d25-gpu-recovery)

GPL-2.0-only. The generic repository includes no private boardview or server firmware dump.

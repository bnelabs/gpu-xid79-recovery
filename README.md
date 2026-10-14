# Capability-based NVIDIA GPU recovery

A motherboard-independent Linux controller for discovering NVIDIA GPUs, inspecting their PCIe reset scope, and attempting recovery after Xid79 without rebooting the host.

**The core contains no board names, GPIO numbers, PCI addresses, driver instruction offsets, or GPU model whitelist.** It identifies NVIDIA VGA/3D GPUs and processing accelerators by PCI vendor/class and reads their advertised capabilities. GeForce, RTX/Quadro workstation, Tesla, and NVIDIA data-center/industrial GPUs are in scope, with no generation or model whitelist. NVIDIA networking, audio, and NVSwitch devices are not treated as GPU targets. Reset selection uses capabilities exposed by the hardware and installed driver.

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

A blocked driver teardown can prevent kernel reset. Driver-specific recovery belongs in a version-checked adapter.

## Workstation, industrial, and data-center scope

Discovery accepts NVIDIA VGA/3D classes (`03xx`) and processing accelerator class (`1200`), including headless compute GPUs. This is discovery support, not a claim that every listed product has been hardware-tested.

Exploration also queries driver-reported architecture, UUID, MIG mode, virtualization mode, fabric state, and topology. Unsupported/unavailable fields remain unknown. Targeted NVIDIA reset is still the first standard route. As documented by [NVIDIA](https://docs.nvidia.com/deploy/nvidia-smi/index.html), NVLink peers and Fabric Manager can affect reset scope. The controller never automatically expands a reset to peer GPUs or stops Fabric Manager/DCGM. If targeted reset fails, generic PCI fallback is refused on multi-GPU hosts, when NVLink is detected, when MIG is enabled, or in a reported virtualized GPU mode. A verified lifecycle adapter is required for those broader recovery cases. MIG partitions, vGPU tenants, and NVSwitch fabrics are not destroyed/reconfigured by the generic core.

The default `services` list is empty. Configure the GPU workers you want drained before recovery. Platform adapters must implement any additional workload lifecycle they require.

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

Profiles in `/etc/gpu-recovery/profiles.d/` need board and PCI identity constraints, a root-managed backend executable, and a read-only `check_command`. The check must validate wiring, topology, kernel, driver, and resource requirements before returning success. An optional `validation_command` supplies CUDA verification when the profile is selected. The default installer adds no platform profiles and preserves existing administrator profiles.

For compatible hardware, the optional [MS-7D25 profile](profiles/ms7d25.json) and [configuration example](examples/ms7d25-adapter.json) integrate the separately installed [platform adapter](https://github.com/bnelabs/ms7d25-gpu-recovery). Install the profile with `sudo ./install.sh --profile ms7d25`. Its hardware and version constraints apply only to that adapter.

## Automatic operation

After configuring an explicit `watch_gpus` allowlist:

```bash
sudo systemctl enable --now gpu-recovery-generic.service
sudo journalctl -u gpu-recovery-generic.service -f
```

The watcher reads new kernel Xid79 events, resolves their GPU PCI address, and applies the same recovery policy. Each GPU is limited to three attempts per hour. A failure suppresses further attempts for that GPU until watcher restart. Unknown GPUs and other Xids are ignored.

Run only one recovery watcher per GPU to avoid concurrent reset attempts.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

Tests cover nonhardcoded PCI addresses, shared-bridge storage refusal, blocked detach stopping subsequent operations, original-driver rebind order, command deadlines, Xid targeting, root-managed command checks, and CUDA UUID isolation. Synthetic PCI/sysfs tests are not hardware-recovery claims.

Hardware validation covers an RTX 3090 on an MS-7D25 board through the optional platform adapter: recovery from unreachable PCI configuration space, successful NVML and CUDA transfer checks, and unchanged Linux boot identity. This validates the adapter integration; it does not establish recovery support for other boards or GPUs.

Standard NVIDIA/kernel reset paths, enterprise fabrics, MIG, and virtualized deployments require further hardware validation. Automated tests use synthetic fixtures for those policy checks. Discovery coverage is broader than hardware-tested recovery coverage.

## References

- [NVIDIA reset capabilities and limits](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [Linux PCI reset/sysfs ABI](https://docs.kernel.org/6.14/admin-guide/abi-testing.html)
- [Linux PCI error recovery](https://docs.kernel.org/PCI/pci-error-recovery.html)
- [MS-7D25 actual recovery and driver investigation](https://github.com/bnelabs/ms7d25-gpu-recovery)

GPL-2.0-only.

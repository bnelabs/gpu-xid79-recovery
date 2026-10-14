# Capability-based NVIDIA GPU recovery

A motherboard-independent Linux controller for discovering NVIDIA GPUs, inspecting their PCIe reset scope, and attempting recovery after Xid79 without rebooting the host.

**The core contains no board names, GPIO numbers, PCI addresses, or driver instruction offsets.** It discovers standard kernel/NVIDIA interfaces and accepts explicitly configured platform adapters when independent PERST or power control is required.

Motherboard independence does not mean every GPU failure is recoverable in software. An electrically disconnected GPU may need a reset/power mechanism its motherboard does not expose. The controller reports that failure instead of inventing a successful recovery or rebooting the server.

## Install and inspect

Python 3.10+, Linux PCI sysfs, NVIDIA driver tools, and systemd are required. No Python packages are needed.

```bash
git clone git@github.com:bnelabs/gpu-xid79-recovery.git
cd gpu-xid79-recovery
sudo ./install.sh
gpu-recovery discover
gpu-recovery plan --gpu 0000:07:00.0
```

The PCI address above is an example: use a discovered address. `discover` and `plan` are read-only. If exactly one NVIDIA display-class GPU exists, `--gpu` can be omitted. Recovery and watching require root.

Installation does not enable a watcher or reset hardware. It installs the CLI at `/usr/local/sbin/gpu-recovery` and a default empty configuration at `/etc/gpu-recovery/config.json`.

## Standard recovery

```bash
sudo gpu-recovery recover --gpu 0000:07:00.0 --config /etc/gpu-recovery/config.json
```

The controller:

1. Discovers the GPU's slot functions, current drivers, parent bridge, and exposed reset methods.
2. Refuses PCI reset or lifecycle-backend fallback if the reset scope contains another slot, storage, a bridge, or unknown devices. A targeted NVML reset can still be attempted on a shared bridge.
3. Stops the configured services that were active. It does not kill arbitrary processes.
4. Attempts NVIDIA's reset targeting only the selected PCI address. It never expands the request to peer GPUs. Visible CUDA clients or an NVIDIA “in use” refusal abort recovery.
5. If needed, invokes a configured lifecycle backend. Without a backend, detaches only this GPU slot's functions, tries kernel function/subordinate reset, and rebinds the original drivers.
6. Checks NVML and runs an optional workload validator pinned to the selected GPU UUID through `CUDA_VISIBLE_DEVICES`.
7. Restores previously active services and checks that Linux boot identity is unchanged.

A timed-out kernel command stops further reset attempts. SIGKILL does not necessarily terminate a kernel task stuck in D-state. If detach/reset is blocked, the controller does not issue another rebind/reset against that locked device. It does not globally unload NVIDIA modules, remove/rescan PCI trees, write arbitrary registers, guess GPIOs, or invoke reboot/suspend.

The generic kernel path may fail in the same NVIDIA teardown waits observed on the original server. Driver-specific kernel patches belong in a version-checked adapter, not the generic core.

## Configure workloads and validation

Edit the root-owned `/etc/gpu-recovery/config.json`:

```json
{
  "watch_gpus": ["0000:07:00.0"],
  "services": [
    {"unit": "my-gpu-worker.service"},
    {"user": "alice", "unit": "llama-server.service"}
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

## Automatic operation

After configuring an explicit `watch_gpus` allowlist:

```bash
sudo systemctl enable --now gpu-recovery-generic.service
sudo journalctl -u gpu-recovery-generic.service -f
```

The watcher reads new kernel Xid79 events, resolves their GPU PCI address, and applies the same recovery policy. Each GPU is limited to three attempts per hour. A failure suppresses further attempts for that GPU until watcher restart. Unknown GPUs and other Xids are ignored.

**Run only one recovery watcher for a GPU.** On the verified MS-7D25 server the original watcher remains enabled, and this generic watcher is installed but disabled, avoiding simultaneous recovery controllers.

## Validation

```bash
python3 -m unittest discover -s tests -v
```

Tests cover nonhardcoded PCI addresses, shared-bridge storage refusal, blocked detach stopping subsequent operations, original-driver rebind order, command deadlines, Xid targeting, root-managed command checks, and CUDA UUID isolation. Synthetic PCI/sysfs tests are not hardware-recovery claims.

On the original server the generic controller completed a real, full hardware reset/reload cycle through its verified MS-7D25 lifecycle adapter, then passed its own NVML and CUDA validator. See [live-adapter-test.log](live-adapter-test.log). Linux boot ID stayed unchanged. This test was performed on the recovered, healthy GPU; it did not inject another Xid79.

The standard NVML and generic sysfs recovery paths have not been hardware-validated on other motherboards. No universal recovery guarantee is claimed. The original real-Xid79 recovery evidence is in the board-specific repository.

## References

- [NVIDIA reset capabilities and limits](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [Linux PCI reset/sysfs ABI](https://docs.kernel.org/6.14/admin-guide/abi-testing.html)
- [Linux PCI error recovery](https://docs.kernel.org/PCI/pci-error-recovery.html)
- [MS-7D25 actual recovery and driver investigation](https://github.com/bnelabs/ms7d25-gpu-recovery)

GPL-2.0-only. The generic repository includes no private boardview or server firmware dump.

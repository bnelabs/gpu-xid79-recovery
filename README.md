# NVIDIA GPU recovery

A Linux script that attempts to bring an NVIDIA GPU back online after Xid79 without rebooting. It checks the board, PCIe topology, and available reset methods before starting recovery.

Works with NVIDIA GPU and compute-accelerator device classes, including GeForce, RTX/Quadro, Tesla, and data-center GPUs. Recovery depends on the hardware and driver. Some failures still need a power cycle.

## Install

Requires Linux, Python 3.10+, NVIDIA driver tools, and systemd. There are no extra Python dependencies.

```bash
git clone git@github.com:bnelabs/gpu-xid79-recovery.git
cd gpu-xid79-recovery
sudo ./install.sh
```

This installs `gpu-recovery` and creates `/etc/gpu-recovery/config.json`. The watcher starts only when you enable it.

## Inspect and recover

List GPUs, then inspect the one you want to recover:

```bash
gpu-recovery discover
sudo gpu-recovery explore --gpu 0000:07:00.0
```

Replace the example PCI address with yours. You can omit `--gpu` on a single-GPU machine. `explore` prints a JSON report with board and BIOS details, PCIe capabilities, reset methods, driver information, and any matching platform adapter. It also includes MIG, virtualization, and NVLink information when available. `plan` is an alias for `explore`; neither resets the GPU.

To start recovery:

```bash
sudo gpu-recovery auto --gpu 0000:07:00.0 --config /etc/gpu-recovery/config.json
```

`recover` is an alias for `auto`. Both inspect the hardware on every run. If an installed platform profile matches and passes its compatibility check, the script can use that adapter. Otherwise, it tries a targeted NVIDIA reset, followed by a kernel reset of the GPU's PCI functions if needed.

Discovery reports advertised FLR, PCI configuration reachability, the driver's recovery action, and upstream slot power-control capability. It also lists existing I2C adapters, NVIDIA USB candidates, and MCTP links for investigating out-of-band recovery. An I2C controller on the GPU itself depends on the GPU's PCIe connection.

The NVIDIA reset normally uses FLR. If the driver reports `Bus Reset`, the script requests that mode on an isolated single-GPU setup. You can also set `"nvidia_reset_mode": "flr"` or `"bus"` in the config. An OS permission rejection stops the attempt.

For a closer look at firmware reset controls, install ACPICA's `iasl` tool and run:

```bash
sudo gpu-recovery explore --deep --gpu 0000:07:00.0
```

This decodes the machine's DSDT/SSDT tables, locates reset and power methods at the GPU and upstream port, and traces literal helper-method calls. GPIO discovery also reads line names, consumers, and polarity flags. The firmware scan runs without a board profile. Its report includes partial-decode errors and unresolved scope information; it does not execute firmware methods. To include it during recovery or watching, use `--deep` or set `"deep_discovery": true` in the config.

Firmware can describe a reset through `_RST`, power resources, or a `_DSD` GPIO mapping. When that description is missing, unnamed GPIO lines cannot identify the physical PERST connection. The scan reports candidates for an adapter; it cannot generate a board's missing wiring information.

Configured GPU workers are stopped before recovery and restarted afterwards. The script checks the GPU through NVML and can run a CUDA test you provide.

## Configure GPU workers

Edit `/etc/gpu-recovery/config.json` to list the services that use your GPU:

```json
{
  "watch_gpus": ["0000:07:00.0"],
  "services": [
    {"unit": "gpu-worker.service"},
    {"user": "alice", "unit": "gpu-worker.service"}
  ],
  "validation_command": ["/usr/local/lib/gpu-tests/cuda-check"],
  "backends": {}
}
```

The default service list is empty. Only listed services that were running are stopped and restarted. Active CUDA clients or an NVIDIA “in use” response will stop the standard recovery path.

`validation_command` is optional. It receives `GPU_BDF` and `CUDA_VISIBLE_DEVICES` set to the selected GPU's UUID, and must return a nonzero exit code on failure. Without it, recovery checks NVML access only.

Configuration files and executables used during recovery, including their parent directories, must be owned by root and not writable by group or others.

## Watch for Xid79

Set `watch_gpus` in the config, then enable the watcher:

```bash
sudo systemctl enable --now gpu-recovery-generic.service
sudo journalctl -u gpu-recovery-generic.service -f
```

The watcher handles new Xid79 events for the listed GPUs, with a limit of three recovery attempts per GPU per hour. A failed attempt disables further attempts for that GPU until you restart the watcher. Run one watcher per GPU.

## Recovery limits

PCI reset requires the GPU's slot functions to be isolated from other devices behind the bridge. A blocked driver operation or a command timeout stops further reset attempts.

On multi-GPU, NVLink, MIG, and virtualized systems, a failed NVIDIA reset needs a platform adapter before the script can proceed. The generic path leaves peer GPUs, MIG partitions, and Fabric Manager alone. See [NVIDIA's reset documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html) for the hardware-specific requirements.

Boards that need a separate PERST signal or slot power control also need an adapter. These controls depend on the board's wiring and firmware.

## Platform adapters

An adapter handles the reset, PCI restoration, and driver cleanup for a particular platform. It must check hardware identity, reset scope, and supported kernel and driver versions, then return a nonzero exit code if recovery fails.

Configure one under `backends`:

```json
{
  "backends": {
    "0000:07:00.0": {
      "command": ["/usr/local/sbin/platform-gpu-reset"],
      "match": {"board_vendor": "Board Vendor", "board_name": "Board Model"},
      "requires_single_gpu": true,
      "prefer_backend": true
    }
  }
}
```

`prefer_backend` runs the adapter before trying NVIDIA reset. `requires_single_gpu` limits it to a single-GPU host, which is useful for adapters that unload the NVIDIA driver. Use `--backend-only` to run an adapter directly through the controller.

The adapter receives `GPU_BDF`, `GPU_BRIDGE`, and `GPU_SLOT_FUNCTIONS` (a JSON array). Commands use argument arrays and run without a shell.

For automatic matching, put a profile in `/etc/gpu-recovery/profiles.d/` with board and PCI identity constraints, a backend command, and a read-only `check_command`. The compatibility check runs before recovery. If several profiles match, select a backend explicitly in the config. Profiles can also supply a `validation_command`.

The optional [MS-7D25 profile](profiles/ms7d25.json) uses the separately installed [MS-7D25 adapter](https://github.com/bnelabs/ms7d25-gpu-recovery). Install that profile with:

```bash
sudo ./install.sh --profile ms7d25
```

See the [adapter configuration example](examples/ms7d25-adapter.json). The default installation includes no board profiles.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests cover device discovery, reset scope, timeouts, driver rebinds, profile selection, enterprise GPU checks, and workload validation.

Hardware testing so far covers an RTX 3090 on an MS-7D25 board using its platform adapter. It recovered from unreachable PCI configuration space and passed NVML and CUDA transfer checks without rebooting. Other boards, enterprise fabrics, MIG, and virtualized deployments still need hardware testing.

## References

- [NVIDIA reset documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
- [Linux PCI sysfs ABI](https://docs.kernel.org/6.14/admin-guide/abi-testing.html)
- [Linux PCI error recovery](https://docs.kernel.org/PCI/pci-error-recovery.html)

GPL-2.0-only.

# Experimental NVIDIA lost-device teardown candidate

`nvidia-615.71.09-lost-device.patch` ports the six commits in [atassis's eGPU series](https://github.com/atassis/open-gpu-kernel-modules/tree/b1116823b3c8b773baf9d3a2b20ddcc921a28832) to NVIDIA open kernel modules **615.71.09**, then adds all-ones GET checks to the FIFO and notifier waiters. The original series and authors retain their attribution and licenses.

This candidate is not installed by `install.sh`. It is a driver teardown experiment, not a complete Xid79 recovery solution.

## Build

```bash
git clone https://github.com/NVIDIA/open-gpu-kernel-modules.git /path/to/nvidia-source
git -C /path/to/nvidia-source checkout 61dcc93722ecb418bb5f2e00923f05b4b8051dd1
cd /path/to/nvidia-source
git apply --check /path/to/gpu-xid79-recovery/patches/nvidia-615.71.09-lost-device.patch
git apply /path/to/gpu-xid79-recovery/patches/nvidia-615.71.09-lost-device.patch
make -j2 modules
```

The base is the commit behind tag `615.71.09`. Other versions need a reviewed port. Building requires the normal NVIDIA source dependencies and kernel headers. These instructions build modules; they do not replace the running driver.

## What changed

- A GPU-unavailable event sets a flag that display waiters can read without taking the modeset lock.
- FIFO and notifier waits stop when that flag is set. A no-progress backstop also marks a stalled device lost.
- The FIFO loss path resets its software ring accounting so callers cannot underflow the free count and keep advancing beyond the buffer.
- An all-ones GET is treated as loss instead of being clamped into apparent free space. This additional guard also returns notifier failure without writing completion.
- The imported series adds DRM unplug checks, output cleanup, removed-device accounting guards, and lost-device exits from selected Falcon teardown paths.

The rebase preserves the newer driver's content-protection fields and infoframe handling. The original series targeted 610.43.02; its author's hardware result does not validate this port.

## Validation completed

The patch applies to a pristine 615.71.09 index and built all five modules against Linux 7.0.0-38-generic. The build emitted compiler and objtool/rethunk warnings, and BTF generation was skipped because `vmlinux` was unavailable. Those warnings have not been isolated against an unpatched baseline. No modules were installed or loaded for this validation.

Run the compiled source harness after applying the patch:

```bash
python3 /path/to/gpu-xid79-recovery/experiments/replay_pushbuffer.py /path/to/nvidia-source
```

Fifteen scenarios matched expectations. They include normal operation, a loss event during a wait, all-ones GET, a stalled reachable queue, notifier polling/completion, and slow progress beyond the backstop interval. The lost-device ring test submits 100,000 methods across payload sizes 1–63 dwords to a modeled 64-dword ring with a reserved jump word; its bounds and guard checks pass.

The harness compiles hash-checked functions from the patched source, including the actual inline method writer. MMIO, notification delivery, time, and header encoding remain modeled. It is not a GPU emulator or a concurrency test.

## Still unresolved

The no-progress heuristic can misclassify an unusually long legitimate stall. The all-ones guard treats a single invalid read as loss; transient reads need hardware testing. Callback lifetime, concurrent teardown, active CUDA/UVM clients, DMA cleanup, and architecture-specific firmware teardown need further validation. The void notifier API still does not provide complete failure propagation.

The source review includes a later compute-only GB206 report that remains outside the original series' demonstrated coverage. No recovery guarantee is made for Ada, Blackwell, enterprise fabrics, or other boards.

This patch supplies no physical reset transport. Restoring a lost PCIe link still needs a working upstream reset or an independently controlled reset/power path, followed by fresh driver initialization and CUDA correctness checks.

See [driver-removal research](../docs/driver-removal.md) for remaining UVM and callback-lifetime tests and the current limits of proposed GPU emulators.

The candidate core module was subsequently exercised inside isolated Linux guests against dead NVIDIA-identity PCI fixtures. All five ID profiles rejected early probe without hanging. No module was loaded on the physical GPU host. This does not validate the patched modeset/UVM teardown paths; see the [probe lab](../experiments/qemu/NVIDIA-PROBE.md).

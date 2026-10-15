# Low-level reset source replay

The experiment in `experiments/replay_nvidia_reset.py` runs four actual NVIDIA GPU Admin Tools methods with modeled PCI, MMIO, FSP, and upstream-bridge responses. It imports no vendor hardware module, maps no BAR, and performs no device operation.

It is source-level fault injection. It does not emulate the NVIDIA ASIC or execute GSP/VBIOS firmware. A modeled bridge that restores a link is an input assumption, not a demonstrated hardware outcome.

## Run

```bash
git clone https://github.com/NVIDIA/gpu-admin-tools.git /path/to/gpu-admin-tools
git -C /path/to/gpu-admin-tools checkout 44f261a7ebff96559488230b420e4a3035b30d58
python3 experiments/replay_nvidia_reset.py /path/to/gpu-admin-tools
```

No root privileges or extra Python packages are required. Each method's source hash is checked before its AST is compiled in a restricted namespace. The experiment also checks the source's Hopper-only reset-coupling gate. Changed methods are refused until reviewed.

The 12 cases cover immediate/transient PCI readiness, all-ones config reads, perpetual configuration retry, upstream reset success/failure, reset-coupling permissions, unsupported devices, loss of the in-band FSP transport, and an unexpected firmware reply. Expected failures count as passing cases only when they match the stated expectation.

## Reset coupling

NVIDIA GPU Admin Tools has `--set-next-sbr-to-fundamental-reset`. At the reviewed revision, its actual capability gate is `self.is_hopper`; code mentioning Blackwell register offsets inside the method does not enable it on Blackwell.

The method checks the force-reset-coupling setting and its in-band permission. When permitted, it enables coupling as needed and sends an FSP one-shot command. This is a possible GPU-side preparation mechanism on supported Hopper hardware, without requiring motherboard GPIO decoding. It still needs working firmware transport when configured, applicable permissions, a safe upstream reset scope, and complete driver/PCI restoration.

Do not apply it to GeForce by removing the capability check. It has not been hardware-tested in this project and is not enabled by the recovery controller. Reset coupling also needs special care on self-hosted GPU/CPU systems: the kernel driver's recovery decision can require a system reboot when the GPU reset is coupled.

## Limits found

The vendor FLR method loops while PCI configuration returns `0xffff0001` without a deadline in that loop. The experiment's simulated step budget catches the perpetual-retry case. This demonstrates source behavior under the modeled response; it is not a measured hardware hang. The production recovery controller already places deadlines around external operations and stops further device calls after a timeout. A deadline cannot terminate a kernel task stuck in uninterruptible sleep.

The broken-device recovery method uses the upstream bridge's SBR, followed by PCI sanity checks. Its success therefore depends on whether that reset restores communication. No independent hidden GPU transport was identified in this path.

## Sources

- [NVIDIA GPU Admin Tools](https://github.com/NVIDIA/gpu-admin-tools/tree/44f261a7ebff96559488230b420e4a3035b30d58)
- [Reset and coupling methods](https://github.com/NVIDIA/gpu-admin-tools/blob/44f261a7ebff96559488230b420e4a3035b30d58/nvidia_gpu_tools.py)
- [Upstream bridge reset](https://github.com/NVIDIA/gpu-admin-tools/blob/44f261a7ebff96559488230b420e4a3035b30d58/pci/bridge.py)
- [Driver recovery decision for coupled resets](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/gpu.c)

## Compiled notifier-wait experiment

```bash
python3 experiments/replay_notifier_wait.py /path/to/open-gpu-kernel-modules
```

This requires a C compiler and the reviewed NVIDIA kernel-module source at commit `ca2d03e6fafd4d4b56b383497d931c25b283bfcf`. The tool hash-checks the actual `EvoCheckNotifier` function, then compiles original and candidate variants against modeled GET, notifier, and timeout inputs. Compilation and execution happen in a temporary directory. It builds no kernel module.

Twelve cases compare completed/nonblocking notifications, a drained queue with a lost notification, a slow reachable queue, all-ones GET, and a reachable stalled GET. The original exhausts the modeled step budget for all-ones GET. The candidate returns failure after the existing short timeout without changing the notifier. Both retain the same modeled normal behavior; both still stall for the modeled reachable-but-nonprogressing GET.

This candidate is not deployed. The outer `nvEvoWaitForCoreNotifier` API returns void and ignores the internal result. Other waits, including `nvEvoMakeRoom`, also need review. Returning from one wait does not establish a complete driver teardown or GPU recovery path. The test does not establish which MMIO values occurred in a real failure.


## Rebased FIFO and notifier paths

The separate [driver candidate](../patches/README.md) has a broader compiled harness:

```bash
python3 experiments/replay_pushbuffer.py /path/to/patched-615.71.09-source
```

It exercises actual FIFO allocation, method writer, loss-flag, and notifier functions from that patch. Fifteen modeled scenarios include a 100,000-method ring-bounds stress case and a 61-second slow-progress case. Method hashes bind the replay to the reviewed candidate. This does not test physical GPU reset, concurrency, or the full driver teardown.

# PCIe AER recovery and driver callbacks

Linux can reset a PCIe link as part of Advanced Error Reporting (AER) recovery.
That does not finish device recovery: the kernel also coordinates driver
quiescing and reinitialization through `pci_error_handlers`. Downstream Port
Containment (DPC) can isolate a failing hierarchy before that recovery runs.
Neither mechanism guarantees that inaccessible GPU hardware will respond again.
See the [kernel recovery interface](https://docs.kernel.org/PCI/pci-error-recovery.html)
and [AER guide](https://docs.kernel.org/PCI/pcieaer-howto.html).

The reviewed NVIDIA open driver registers no `.err_handler` in `nv_pci_driver`.
This was checked against upstream HEAD
`ca2d03e6fafd4d4b56b383497d931c25b283bfcf` on October 15, 2026. See
[the driver registration](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/kernel-open/nvidia/nv-pci.c).
Adding callbacks that simply return success would not initialize a GPU or its
firmware. A usable NVIDIA implementation needs driver-owned quiescing,
context invalidation, hardware accessibility checks, firmware reinitialization,
and a defined outcome for applications holding old device references.

## Controlled guest experiment

The QEMU lab can inject a fatal Surprise Down error status into its first root
port. This uses QEMU's monitor command on a private Unix socket. It cannot
inject errors into host devices. The experiment does not disable the physical
link or reproduce a real GPU fault; it exercises guest-kernel AER handling.

| Guest and endpoint driver | Observed result |
| --- | --- |
| ARM64 Linux 6.12.107, unbound test endpoint | Link reset reported; recovery failed because no `error_detected` callback |
| x86-64 Linux 7.0.0-38, unbound test endpoint | Same failure |
| x86-64 Linux 7.0.0-38, synthetic error-aware test driver | `error_detected` → link reset → `slot_reset` → `resume`; kernel reported success |

Both endpoint identities remained accessible and guest boot IDs stayed unchanged.
The error-aware experiment also checks that the peer on the other bridge receives
no test-driver recovery callbacks. Identity access does not establish that device
workloads survived or that an electrical link failure was repaired.

`aer_test_driver.c` binds only to QEMU PCI test devices `1b36:0005`. It has no
NVIDIA support and is never installed or loaded on the host by the lab runner.
Its simple callback behavior is appropriate only for this synthetic endpoint.
The default installer does not install it.

## Reproduce

Use the [QEMU lab prerequisites](../experiments/qemu/README.md). For the case
without callbacks:

```bash
python3 experiments/qemu/run.py --aer --kernel /path/to/vmlinuz \
  --output /tmp/aer-without-driver.json
```

For the positive comparison, build the test module against headers matching the
**guest** kernel, in a separate build directory:

```bash
mkdir -p /tmp/aer-test-build
cp experiments/qemu/aer_test_driver.c /tmp/aer-test-build/
printf 'obj-m += aer_test_driver.o\n' > /tmp/aer-test-build/Makefile
make -C /lib/modules/GUEST_KERNEL_VERSION/build M=/tmp/aer-test-build modules
python3 experiments/qemu/run.py --aer --kernel /path/to/vmlinuz \
  --aer-driver /tmp/aer-test-build/aer_test_driver.ko \
  --output /tmp/aer-with-driver.json
```

The runner copies the module into the temporary guest initramfs; guest init loads
it there. `--aer` also works with the ARM64 kernel/userspace options. An ARM64
driver comparison needs a module built for that guest architecture and kernel;
only the no-driver ARM64 case has been run here.

`passed` means the expected experiment outcome occurred, including failure in
the no-callback case. `nvidia_recovery_verified` is always false. No test loads an
NVIDIA driver or executes CUDA. QEMU's injection command is documented in its
[monitor command definitions](https://github.com/qemu/qemu/blob/master/hmp-commands.hx).

## Integrated diagnostics

The explorer now reads AER counters for the target and its immediate upstream
port. PCI capability decoding also reports link-disable/training/activity bits,
AER error registers, and DPC trigger/reason/busy status where configuration space
is readable. Missing registers remain unknown. Counters are historical, and zero
counts do not exclude a link fault or firmware-owned error handling.

These additions are read-only. They do not clear error status, enable DPC,
force native AER ownership, retrain a link, inject a host error, or register
NVIDIA driver callbacks. The recovery controller still needs a supported reset
path and successful post-reset verification.

See also [upstream link retraining](link-retraining.md) for port-side training diagnostics and the Linux 2.5 GT/s workaround.

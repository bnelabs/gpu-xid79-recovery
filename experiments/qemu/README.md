# QEMU reset lab

Boot a disposable Linux guest and run the unchanged recovery controller against
two QEMU PCI test devices. No host devices, network, or shared directories are
passed to the guest. QEMU runs as the current user with software CPU emulation.
The controller runs as root **inside the guest**.

The lab checks two layouts:

* Separate bridges: resetting the target clears its test counter while the peer
  counter stays unchanged. The guest boot ID must remain unchanged.
* Shared bridge: the controller must refuse recovery before writing to sysfs.
  Both counters must remain unchanged.

These are real guest-kernel PCI resets, not a fake sysfs tree. Three parts are
deliberately substituted: discovery admits the synthetic target, `nvidia-smi`
reports an unsupported reset with no compute clients, and final verification
checks test-device counters instead of NVML/CUDA. No NVIDIA driver is loaded in
the guest. Driver teardown, GSP firmware, physical link failures, GPIO wiring,
and NVIDIA recovery are outside this test.

## Run on x86-64

Requires Python 3, a static `/usr/bin/busybox`, `cpio`, `ldd`, QEMU's x86 system
emulator, and a readable Linux bzImage with built-in PCI, initramfs, sysfs, and
devtmpfs support. The script copies the host Python runtime into its temporary
initramfs. It does not install anything on the host.

```bash
python3 experiments/qemu/run.py --kernel /path/to/vmlinuz --output /tmp/isolated.json
python3 experiments/qemu/run.py --kernel /path/to/vmlinuz --shared --output /tmp/shared.json
```

## Run an ARM64 guest on an x86-64 host

Requires `qemu-system-aarch64` and `dpkg-deb`. Prepare the recorded Debian ARM64
kernel and userspace without installing ARM64 packages on the host:

```bash
python3 experiments/qemu/prepare_arm64.py /tmp/arm64-reset-lab
python3 experiments/qemu/run.py --arch aarch64 \
  --kernel /tmp/arm64-reset-lab/linux --userspace /tmp/arm64-reset-lab/root \
  --output /tmp/arm64-isolated.json
python3 experiments/qemu/run.py --arch aarch64 --shared \
  --kernel /tmp/arm64-reset-lab/linux --userspace /tmp/arm64-reset-lab/root \
  --output /tmp/arm64-shared.json
```

Downloads are checked against `arm64-inputs.json`. The Debian installer kernel
URL changes over time; a hash mismatch stops preparation. If an input disappears
or changes, obtain the recorded file from an archive or review and update the
manifest before using a newer version.

Each run writes a JSON result and a `.console.log` beside it. A failed assertion,
missing guest result, or QEMU failure returns a nonzero exit code. Guest execution
has a 180-second timeout, with console output retained even on timeout.

For the separate slot shutdown experiment, add `--slot-power` without `--shared`.
It checks removal scope and boot continuity, and records whether the target
returns after power-on. Its `passed` field is not a recovery result. Both tested
platforms removed the target but failed to restore it; see [results](../../docs/slot-power.md).

For fatal AER status injection into the guest root port, use `--aer`. An optional
`--aer-driver /path/to/aer_test_driver.ko` loads the synthetic testdev driver inside
the guest to compare error-callback behavior. See [build instructions and results](../../docs/aer-recovery.md).
These modes cannot be combined with `--shared` or `--slot-power`.

`--scope-peer nvme` uses an emulated NVMe controller as the neighbor. Add
`--shared` to test refusal when storage shares the target bridge. `--scope-peer gpu`
admits both synthetic endpoints to test the multi-GPU fallback guard. These modes
cannot be combined with AER or slot-power experiments. See [scenario results](../../docs/hardware-scenarios.md).

`--missing` tests logical target removal and rediscovery through the surviving
bridge's child-bus rescan interface. It uses separate bridges and cannot be
combined with other experiment modes. See [scope and limits](../../docs/missing-device.md).

## Results recorded on October 15, 2026

| Platform | Guest kernel | Isolated target | Shared bridge |
| --- | --- | --- | --- |
| x86-64 Q35 | 7.0.0-38-generic | Reset passed; peer unchanged | Refused; no sysfs writes |
| ARM64 virt / Cortex-A57 | 6.12.107+deb13-arm64 | Reset passed; peer unchanged | Refused; no sysfs writes |

QEMU version: 10.2.1. Both layouts preserved the guest boot ID.

ARM `virt` is a generic virtual platform, not a model of a particular server.
These results establish behavior on these virtual machines, not all ARM or x86
boards. Adding a virtual GPU that always obeys reset would test that assumption,
not establish that real NVIDIA firmware obeys it after Xid79.

References: [QEMU PCI test device](https://www.qemu.org/docs/master/specs/pci-testdev.html),
[ARM virt platform](https://www.qemu.org/docs/master/system/arm/virt.html),
[QEMU reset framework](https://www.qemu.org/docs/master/devel/reset.html).

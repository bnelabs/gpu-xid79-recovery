# Kernel reset lifecycle and hotplug coordination

The presence of `reset_subordinate` does not establish which kernel reset path
it uses. Linux commit
[`8238cb69c01fe4dbb4e3be277fff3ed680ac0108`](https://github.com/torvalds/linux/commit/8238cb69c01fe4dbb4e3be277fff3ed680ac0108)
changes the interface to use the bridge reset helper, allowing hotplug slot
handling to suppress reset-induced link events. The commit describes spurious
hotplug events caused by the older direct bus-reset path. Backports and vendor
changes make a release number alone insufficient to establish its presence.

## Trace the actual path in a disposable guest

The QEMU lab's `--trace-reset` mode mounts tracefs and registers kprobes **inside
the guest**. It records available reset functions, registration failures, trace
hits and the trace text while running the default isolated synthetic-device
reset. It never installs probes or resets hardware on the host.

```bash
python3 experiments/qemu/run.py --trace-reset --kernel /path/to/vmlinuz \
  --output /tmp/reset-trace.json
```

This mode requires guest tracefs and kprobe-event support. It cannot be combined
with other lab modes. Missing symbols are reported, and the experiment fails if
it observes no registered reset call. Symbol absence alone does not prove a
source patch is absent: compilers may inline functions or downstream kernels may
implement a different fix.

Recorded results:

| Guest | `__pci_reset_bus` | `pci_bridge_secondary_bus_reset` | `pciehp_reset_slot` |
| --- | --- | --- | --- |
| x86-64 Linux 7.0.0-38-generic | 1 hit | 1 hit | 0 hits |
| ARM64 Linux 6.12.107+deb13-arm64 | 1 hit | 1 hit | 0 hits |

Both synthetic targets reset their counter from 1 to 0, peers stayed at 1, and
boot IDs remained unchanged. These are observed calls in these fixtures. The
trace does not prove a spurious hotplug event occurred, attribute every possible
reset call by BDF, or validate physical PCIe timing and NVIDIA recovery. No patched
kernel comparison has been performed.

## DPC is a separate lifecycle

Linux's [DPC implementation](https://github.com/torvalds/linux/blob/8238cb69c01fe4dbb4e3be277fff3ed680ac0108/drivers/pci/pcie/dpc.c)
waits for the contained link to become inactive, handles root-port busy state,
clears trigger status and waits for the secondary bus to become ready. DPC and
hotplug also coordinate recovery completion. Manually clearing the trigger bit
would bypass that lifecycle. A successful link reset still needs successful
endpoint-driver recovery, as the earlier [AER lab](aer-recovery.md) demonstrates.

The current QEMU lab has not simulated DPC containment or firmware-owned EDR.
Its AER injection is not a substitute for those tests. No host DPC state was
changed, and no automatic DPC-clearing backend was added.

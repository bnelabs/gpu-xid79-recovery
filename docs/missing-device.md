# When the GPU disappears from PCI enumeration

The generic controller requires a currently discovered NVIDIA PCI function.
If that function has been removed from the kernel device tree, `inspect` refuses
it and the generic watcher does not proceed with recovery. This is different
from a present sysfs device whose configuration reads return all ones.

That limitation is intentional today: a PCI address from an earlier inventory
is insufficient authority to reset a device that is no longer visible. No
automatic missing-device recovery path has been added.

## Logical removal is not electrical failure

The new QEMU experiment takes an in-memory snapshot of target identity, parent
identity/path, and guest boot ID. It removes the synthetic target through sysfs,
confirms that normal inspection refuses the missing target, checks that the
parent remains unchanged and its enumerated subtree is empty, then rescans the
parent's subordinate bus. The target returns with the same vendor/device ID.
The other endpoint remains accessible and the guest boot ID stays unchanged.

This passed with QEMU 10.2.1 on x86-64 Linux 7.0.0-38 and ARM64 Linux 6.12.107.
It demonstrates rediscovery of a logically removed, still-reachable endpoint.
It does not demonstrate restoration of electrical connectivity, NVIDIA firmware
restart, unique device identity, or functioning CUDA.

```bash
python3 experiments/qemu/run.py --missing --kernel /path/to/vmlinuz \
  --output /tmp/missing-device.json
```

Use the documented ARM64 kernel/userspace options for that architecture. This
mode cannot be combined with the other experiment modes or a shared bridge.
The runner never passes through host PCI devices.

## Rescan scope matters

`/sys/bus/pci/rescan` is global. A PCI **device's** `rescan` file scans its parent
bus; using a root port's file can therefore be broader than its downstream
subtree. The bridge's child-bus interface is:

```text
/sys/bus/pci/devices/BRIDGE/pci_bus/DOMAIN:BUS/rescan
```

The explorer now reports this path as `subordinate_rescan_path` only when a
unique child-bus interface is found. Discovery does not write it. A rescan can
enumerate devices, allocate resources, and trigger driver probes; it is not a
read-only health check and does not itself supply a missing reset signal.
See the [kernel PCI sysfs ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-bus-pci)
and [implementation](https://github.com/torvalds/linux/blob/master/drivers/pci/pci-sysfs.c).

## What a future recovery snapshot needs

A production snapshot would need a trusted store and freshness rules, stable
GPU identity where available, parent identity and physical path, known shared
consumers, a verified reset backend, and boot/firmware/topology invalidation.
After return, matching BDF and vendor/device IDs alone cannot distinguish a
replacement of the same model. Old BAR values must not be restored blindly
after topology or resource allocation changes.

For an eGPU, also establish the enclosure/router mapping and other tunneled
devices. For a multi-GPU server, establish fabric dependencies and tenant
ownership. For a BMC path, establish management endpoint identity and reset
scope. These remain hardware-specific discovery and validation requirements;
the lab snapshot is not installed as recovery authorization.

When firmware, power, or a PCIe fault leaves the endpoint electrically
unreachable, a rescan may find nothing. A separate supported reset/power path is
still necessary. Linux's [error-recovery documentation](https://docs.kernel.org/PCI/pci-error-recovery.html)
also notes that a device can remain inaccessible even after a slot reset.

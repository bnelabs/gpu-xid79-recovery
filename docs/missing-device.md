# When the GPU disappears from PCI enumeration

Generic recovery still requires a currently discovered NVIDIA PCI function.
The optional [prepared domain path](prepared-domain-recovery.md) now supports
missing functions through a boot-bound pre-fault capsule, fresh independent
adapter preflight, narrowly scoped rediscovery and whole-domain verification.
No snapshot alone authorizes a reset, and no generic missing-device reset is
attempted without that explicitly configured lifecycle path.

The experiments below describe the earlier diagnostic checker and establish
why PCI addresses alone are insufficient. They remain useful scope evidence.

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

## Pre-fault topology checker

`experiments/topology_snapshot.py` now captures an isolated slot's PCI identities,
resolved paths, parent, function list, downstream scope and current boot ID. Its
`assess` command works even after the target disappears. It rejects changed boot
identity, changed parent identity/path, unexpected downstream devices, changed
visible target identity/path, and an absent target whose scope is not empty.

```bash
python3 experiments/topology_snapshot.py capture --gpu 0000:01:00.0 > snapshot.json
python3 experiments/topology_snapshot.py assess snapshot.json
```

Both commands are read-only. The output is diagnostic data, not authorization:
`reset_authorized` remains false. The snapshot is not signed or installed in a
trusted recovery store. Identical PCI IDs and topology cannot distinguish a
replacement of the same model. No physical GPU UUID, fabric ownership, service
quiescence or independent controller mapping is established by this diagnostic tool.

The missing-device QEMU lab now runs this checker before its experimental scoped
rescan. It still admits a synthetic endpoint and removes it logically. It does
not simulate an electrically lost NVIDIA GPU. Unit tests separately cover changed
boot, changed parent, new peer, remaining slot function and malformed PCI paths.

This provides reusable checks for a future missing-device recovery path. The diagnostic checker itself enables no production reset path; the separate
prepared-domain implementation adds the explicit capsule/adapter contract. NVIDIA's
[drain/remove/discover API](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlZPI.html)
explicitly supports lifecycle coordination with out-of-band tools; those APIs
cannot provide missing physical reset control on their own.

# Upstream link retraining

A surviving root or downstream port can request link retraining without accessing the GPU's BARs. This is a separate operation from FLR, secondary-bus reset, PERST and power removal. It can address some failed link-training conditions; it does not restart a GPU's firmware or repair an unpowered endpoint.

Linux contains `pcie_failed_link_retrain()` for a documented switch interoperability problem. It requires a PCIe downstream port, Link Control 2, Data Link Layer Link Active reporting, and speed capability above 2.5 GT/s. Its failed-link branch checks that the link is inactive and that Link Bandwidth Management Status has been seen. It tries 2.5 GT/s, then attempts to remove that restriction. Failure restores the previous target speed and requests another retrain.

See [the Linux implementation and rationale](https://github.com/torvalds/linux/blob/master/drivers/pci/quirks.c) and [bandwidth controller](https://github.com/torvalds/linux/blob/master/drivers/pci/pcie/bwctrl.c). These links follow upstream master; this review was performed on 2026-10-15. The workaround was developed for a particular training problem, not as a general NVIDIA Xid79 remedy.

## Explorer fields

The explorer now decodes these fields for both the GPU and its immediate upstream port:

- `data_link_active_reporting`: whether Link Capabilities says the active-status bit is implemented.
- `link_bandwidth_management_status`: the current LBMS bit. Linux may already have cleared it and retained a private sticky flag; a clear snapshot does not exclude earlier training failures.
- `current_link_speed_code` and `current_link_width`: raw negotiated speed encoding and lane count.
- `target_link_speed_code`: the requested speed encoding, only when a readable PCIe v2-or-newer Link Control 2 register exists.

An inactive bit on a device that does not implement active reporting cannot diagnose a dead link. Missing register bytes remain unknown. The port's status is particularly useful when the GPU itself cannot answer configuration reads.

This is read-only discovery. It does not invoke the kernel workaround, alter target speed, clear status, or write the retrain bit. A future recovery adapter would need to coordinate with kernel hotplug/AER/DPC and bandwidth-controller ownership, quiesce affected users, establish the entire downstream scope, and verify driver and workload recovery afterward. Blind userspace register writes would omit that coordination.

The existing AER lab already demonstrates that a kernel-reported link reset can succeed while device recovery fails because the endpoint driver lacks recovery callbacks. Link-active status alone is therefore insufficient evidence of NVIDIA recovery. See [AER experiments](aer-recovery.md).

# Recovery through a managed PCIe switch

A managed switch is a possible control point when the GPU no longer answers
host PCIe requests. The management endpoint must survive the particular fault,
and its physical port must be mapped to the GPU and every affected peer. A switch
in the topology does not establish either condition.

## Concrete interface reviewed

Microchip/Microsemi's Switchtec library exposes `switchtec_port_control()` with
DISABLE, ENABLE, LINK_RETRAIN and LINK_HOT_RESET control types. Hot reset has
separate SET/CLEAR flags. The request carries a physical port ID, not a GPU BDF.
The library sends MRPC command `0x8d` with a four-byte request.

The device-opening API supports the PCI management character device, I2C and
UART transports. An I2C/UART route may avoid the failed host data link, but only
if the board actually wires it and its controller remains available. No address
scanning or command probing is performed by this project.

Reviewed commit: `99da3bc799b84ff53abc726c2fdea01379b3c687` (2026-10-09).
Sources: [port-control implementation](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/lib/fabric.c),
[control enums](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/inc/switchtec/fabric.h),
[command number](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/inc/switchtec/mrpc.h),
[transport selection](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/lib/switchtec.c).

Whole-switch hard reset is a different operation. The upstream library explicitly
warns that systems without hotplug support may leave the switch unusable until
reboot. It is not a general no-reboot fallback. Per-port hot reset is also not
proof of a fundamental GPU reset or power removal.

## What discovery reports

`explore` now includes `switch_management`:

- The target's full PCI ancestor chain, with vendor/device/class values.
- Existing `/sys/class/switchtec` management functions, firmware and partition metadata.
- Whether a management function is underneath the target GPU, and common PCI ancestors.
- Explicitly unverified GPU-port mapping and transport survival.

A sibling management function may remain reachable when the GPU fails, but
shared ancestry cannot identify a physical switch port or authorize its reset.
Multi-host partitions and downstream bridges can extend the affected scope
beyond the devices visible in one host's sysfs tree.

A retimer may not enumerate as a PCI function. Absence of a PCI switch or local
management device does not exclude retimers or external management wiring. A
retimer reset may restore signaling without resetting GPU firmware, and may
interrupt the data path while the GPU still has outstanding transactions. It
needs its own platform-specific lifecycle review.

## Backend requirements

A future adapter needs verified switch identity, physical port mapping, command
support, all affected partitions/hosts, driver quiescence and an independent
transport. It must reconcile an ambiguous SET timeout before assuming reset is
released. A successful reply is not successful GPU recovery: re-enumeration,
physical GPU identity, driver initialization and fresh CUDA execution must pass,
with fabric checks where relevant.

The existing generic multi-GPU/shared-scope guards remain in place. This update
adds inventory only. No switch command was sent, no switch backend was installed,
and no NVIDIA recovery through Switchtec hardware has been tested here.

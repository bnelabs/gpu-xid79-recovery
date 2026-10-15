# PCIe slot power and recovery

The generic explorer reports matching Linux hotplug slots in `hotplug_slots`.
It matches the slot's PCI address to the GPU or one of its upstream devices,
then lists all current PCI functions in that subtree. Slot labels are not PCI
addresses. A slot above a bridge may affect other devices as well as the GPU.

Discovery reads `address` and `power`; it never writes the power control.
`target_functions_only` describes the current enumerated PCI topology, not
electrical isolation. It does not establish which auxiliary power supplies,
reset signals, or other hardware share the control. Missing devices cannot be
inventoried from the current tree. `full_gpu_power_removal_verified` remains
false: a slot control does not by itself prove removal of every GPU power rail.

Linux's PCIe hotplug driver removes devices before switching slot power off,
and checks the link and configures devices when enabling a populated slot.
See [the kernel implementation](https://github.com/torvalds/linux/blob/master/drivers/pci/hotplug/pciehp_ctrl.c).
This is a device lifecycle operation, not simply another function reset.

## What happened in QEMU

The isolated slot-power experiment ran on x86-64 Q35 with Linux 7.0.0-38 and
ARM64 virt with Linux 6.12.107 under QEMU 10.2.1:

| Observation | x86-64 | ARM64 |
| --- | --- | --- |
| Target removed after power-off | Yes | Yes |
| Peer identity remained accessible | Yes | Yes |
| Guest boot unchanged | Yes | Yes |
| Power-on sysfs write | Returned success | Returned ENODEV |
| Target returned after power-on | No | No |
| Recovery established | No | No |

QEMU's slot-control implementation can unrealize downstream devices during
shutdown. Merely switching the control back on does not recreate them in these
tests. See [QEMU's PCIe implementation](https://github.com/qemu/qemu/blob/master/hw/pci/pcie.c).
This models a virtual lifecycle; it does not show that a physical hotplug
controller behaves the same way. It does show why a successful control write
must be followed by device identity and workload verification.

Run this experiment with the [QEMU lab](../experiments/qemu/README.md):

```bash
python3 experiments/qemu/run.py --slot-power --kernel /path/to/vmlinuz \
  --output /tmp/slot-power.json
```

The same flag works with the documented ARM64 kernel and userspace options.
It requires a matching isolated guest slot and refuses `--shared`. The JSON
`passed` field means the shutdown experiment met its scope and boot checks;
`recovery_verified` remains false. It never accesses host PCI devices.

## Why there is no automatic slot-power fallback

A usable backend needs a known reset/power domain, handling for blocked driver
removal, independently confirmed power behavior, device return and identity
checks, and workload validation. Current discovery cannot establish all of
those properties. The generic controller therefore reports the interface but
does not automatically toggle it.

Neither a synthetic endpoint nor an advertised power-control bit establishes
that a lost-link NVIDIA GPU will recover. Systems without an exposed suitable
control still need another supported reset interface or a platform adapter.

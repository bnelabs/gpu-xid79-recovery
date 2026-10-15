# Motherboard-independent recovery: engineering contract

A common software service can be motherboard-independent. Guaranteed physical
recovery cannot be obtained from software when the only command path terminates
at an inaccessible GPU. The proposed way to remove board-specific GPIO decoding
is to provide a control path outside that failure domain. This is an engineering
proposal, not a built or qualified hardware product.

## Architecture

```text
scheduler / service controller ---- independent management link ---- controller
          |                                                       |
    prepared identity capsule                          approved GPU power/reset domain
          |                                                       |
     driver lifecycle -------- PCIe data path ------------------- GPU(s)
```

For standalone consumer and enterprise PCIe cards, the candidate is a managed
carrier/interposer with an independently powered controller. A PCIe switch with
documented downstream management may supply reset/link control; it does not
necessarily supply complete power removal. The carrier needs qualified control
of every GPU supply, including slot and auxiliary power, and any backfeed path.
A USB relay on an auxiliary connector or a slot power bit alone is insufficient.
Use purpose-designed, electrically qualified hardware and vendor sequencing;
this document is not a live connector switching procedure.

For SXM/OAM trays and NVSwitch systems, use the documented tray/BMC management
interface and its actual affected domain. A PCIe card interposer is not mechanically
or electrically interchangeable with those modules. A common controller protocol
can cover both implementations; one universal physical adapter cannot be assumed.
Laptops with soldered GPUs require platform support and are outside a PCIe-carrier
claim. Display recovery also requires a qualified display-session lifecycle.

## Control requirements

The management link and controller power must survive loss of the GPU PCIe link.
The controller must retain its mapping to physical GPU serials and the full
reset/power domain independently of current PCI enumeration. Mapping is provisioned
and physically tested, never guessed from a current BDF or a discovered relay.
The controller must enforce exclusive operation ownership and expose durable
operation IDs/status across host and controller restart. An unanswered request
means unknown outcome; reconcile it before retrying or releasing workloads.

For full-power recovery, inventory all supplies and possible backfeed paths.
Measure that each rail actually reaches the approved off condition, with a
qualified discharge interval. An acknowledged switch operation is not an off
measurement. Do not prescribe one voltage threshold or delay for every GPU.
Keep controller power separate from the rails it switches. Demonstrate that
unrelated storage, GPUs, CPUs and management paths remain available.

A fundamental reset path may recover a particular failure without power removal;
qualify that mechanism separately. The checker below intentionally evaluates the
stronger independent full-power proposal, not the effectiveness of every reset.
Physical damage and faults upstream of the approved domain still require repair
or escalation. Independent power control improves coverage; it cannot guarantee
recovery from every cause of Xid79.

## Existing software integration

Use the existing [domain adapter protocol](domain-adapters.md),
[prepared capsules](prepared-domain-recovery.md), and persistent operation journal
in the [Redfish adapter](redfish-adapter.md) where a documented GPU Processor.Reset
resource exists. A new carrier needs its own documented transport adapter and
platform lifecycle helper; no carrier driver is supplied here. Do not translate
an arbitrary power controller into a Redfish Processor.Reset by assumption.

The lifecycle is: prepare identity/topology while healthy; drain the whole approved
domain; establish bounded driver teardown; persist the operation before dispatch;
execute the qualified reset/power sequence; reconcile independent completion;
rescan only recorded child buses; verify returned identities, fresh workload,
partitions and fabric; then release services. Failure at any step retains holds.
A blocked shared host driver can prevent this lifecycle even if the power control
works. Dedicated VFIO/microVM ownership is a possible containment design, but is
not implemented or qualified here and does not create a physical reset mechanism.

## Offline qualification checker

```bash
python3 recovery_qualification.py examples/independent-controller-proposal.json
```

This reads JSON only, makes no hardware calls and runs no configured commands.
Exit 1 means missing declared requirements; exit 2 means invalid input. Exit 0
means all required declarations are true, **not** that the tool observed hardware
or authorized a reset. Both `hardware_verified_by_this_tool` and `reset_authorized`
are always false. The example deliberately fails: no carrier or electrical
qualification has been delivered. Supply names are illustrative, not an exhaustive
pin/rail definition. Omissions cannot be detected from a declarative list alone;
`all_power_and_backfeed_paths_inventoried` needs independent engineering evidence.

Tests enumerate all 512 combinations of control coverage, off-voltage measurement
and discharge measurement for three example supplies. Every incomplete combination
must fail. Other tests remove each lifecycle/control requirement and reject shared
host scope. These are policy tests, not electrical emulation or NVIDIA firmware
recovery tests. Existing QEMU tests cover software orchestration only.

## Bench acceptance needed

1. Qualify a carrier on two different motherboard/root-port platforms with the
   same consumer card; repeat with a standalone enterprise PCIe card. Keep BIOS,
   kernel, driver and GPU firmware versions in each record.
2. Measure control independence, supply discharge and sequencing. Disconnect the
   GPU data path on a dedicated bench while retaining management and collect
   real controller telemetry. Electrical fault injection needs qualified equipment.
3. Exercise a real recoverable lost-link fault, blocked teardown, lost management
   replies, controller/host restart and repeated faults. Verify fresh workloads,
   boot continuity and unrelated device I/O, not just PCI enumeration.
4. Qualify enterprise tray/fabric and display workloads separately. Record failures
   and escalation rates; never extrapolate a standalone headless success to them.

No such carrier hardware or additional physical GPU bench is available in this
workspace. Those experiments remain outstanding. Firmware research can inform
supported device-side reset preparation; it cannot establish absent physical
power wiring or demonstrate recovery without a working command transport.

## References

- [NVIDIA Xid catalog: inaccessible PCIe GPU and recovery guidance](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
- [Linux PCI error recovery and driver reinitialization](https://www.kernel.org/doc/html/latest/PCI/pci-error-recovery.html)
- [Linux PCIe hotplug lifecycle implementation](https://github.com/torvalds/linux/blob/master/drivers/pci/hotplug/pciehp_ctrl.c)

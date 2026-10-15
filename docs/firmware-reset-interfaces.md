# Firmware reset interfaces

Firmware can hide the electrical reset details behind a standard interface. That is more portable than decoding a GPIO table, when the board exposes it.

| Interface | What discovery can establish | Recovery route |
| --- | --- | --- |
| Device ACPI `_RST` | A reset method at the device; kernel `reset_method` may list `acpi` | Kernel PCI reset with explicit `kernel_reset_mode: acpi` |
| ACPI `_PRR` | References to a reset resource and other visible consumers | Shared resource needs a platform lifecycle adapter; no direct AML invocation |
| ACPI `_PR0` / `_PR3` | Power-resource relationships and `_ON` / `_OFF` helpers | Resource presence alone does not establish a safe slot power cycle |
| Intel named PCD / GNVS | Selected reset-pin policy and live pad values | Translate the chipset encoding and validate the GPU/slot association |
| AMD AGESA reset descriptors | Board-assigned reset identifiers in reviewed historical reference code | Resolve the board callback and shared scope; an identifier is not a GPIO number |
| Enterprise NSM / BMC | Existing management transports and platform inventory | Independently identify the GPU endpoint and use a platform adapter |

## ACPI reset mode

Set this in the root-managed controller configuration:

```json
{
  "kernel_reset_mode": "acpi"
}
```

The controller requires `acpi` in the kernel's enabled reset-method list. After draining configured workers and passing the existing client, isolation, and enterprise checks, the kernel fallback detaches the slot functions, temporarily selects `acpi`, requests reset, restores the previous method order, and rebinds the original drivers. This option changes the kernel fallback, not the preceding NVIDIA reset attempt. Active-user and OS-permission refusals still stop recovery.

If an operation blocks, the controller stops. It does not issue restoration or rebind calls against the blocked device. If ACPI reset is not listed, selection fails before detach. The script does not probe for hidden methods by changing the kernel's default method list.

Linux's PCI ACPI reset implementation invokes the device's `_RST`. The presence of `_PRR` alone does not make that PCI method available. The script therefore reports `_PRR` resource candidates separately.

## Shared resources

`explore --deep` reports a `resource_graph`. It follows literal references from the GPU and its upstream ACPI node, finds other static consumers, and traces the resource's reset/power helper methods. Conflicting resource definitions are flagged and excluded from the resource-method trace.

Conditional returns, aliases, missing external objects, and runtime-created packages can hide consumers. A graph with one visible consumer is not proof that a resource affects only that device. Inspect decode status and unresolved relationships before developing an adapter.

## AMD reference code

Historical AMD AGESA headers define a per-port `ResetId` and an `AgesaPcieSlotResetControl` board callback. Ports sharing a physical reset GPIO share an identifier. Copying an identifier into a Linux GPIO operation would be incorrect.

Coreboot's current AMD AOAC ASL template also contains reset/power resources, but these control SoC peripherals. They do not establish a reset connection to a discrete NVIDIA slot. Their value here is demonstrating the standard firmware abstraction and its scope, not supplying a GPU reset sequence.

These are source reviews and synthetic parser/controller tests. They are not hardware recovery tests on AMD motherboards or a survey of every shipping OEM BIOS.

## Sources

- [Linux PCI ACPI reset](https://github.com/torvalds/linux/blob/master/drivers/pci/pci-acpi.c)
- [Kernel reset-method ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-bus-pci)
- [ACPI specification 6.5](https://uefi.org/sites/default/files/resources/ACPI_Spec_6_5_Aug29.pdf)
- [Historical AMD AGESA definitions](https://github.com/coreboot/coreboot/blob/4.11/src/vendorcode/amd/agesa/f15tn/AGESA.h)
- [Coreboot AMD AOAC template](https://github.com/coreboot/coreboot/blob/main/src/soc/amd/common/acpi/aoac.asl)

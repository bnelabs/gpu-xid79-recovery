# Firmware declarations for reset during D3-to-D0

Some firmware declares `FundamentalDeviceResetTriggeredOnD3ToD0` under the
`FDF06FAD-F744-4451-BB64-ECD792215B10` UUID in `_DSD`. Microsoft documents this
interface for directed runtime power management on PCIe root ports in its
[PCIe root-port ACPI guide](https://learn.microsoft.com/en-us/windows-hardware/drivers/pci/dsd-for-pcie-root-ports).
This is a useful platform capability lead. It is not a callable reset command,
and the declaration alone does not establish Linux lifecycle support or GPU
recovery after Xid79.

Deep discovery now retains both `Name` and `Method` forms of `_DSD` that mention
this property. Previously it retained selected GPIO properties but missed this
one. The static parser does not evaluate surrounding conditions or determine the
live namespace. A declaration retained from a disabled branch remains a candidate.

## Offline AML execution

`experiments/replay_acpi_d3_declaration.py` extracts one matching `_DSD` method
from reviewed decompiled ASL and executes it using ACPICA `iasl` and `acpiexec`.
It wraps the method in a small fixture with a modeled `PDSD` gate and optional
`PINI` helper. It does not load the full original namespace, run its initialization,
or access host PCI/GPIO interfaces. The interpreter's default operation-region
simulation is not disabled. This is a reviewed-input research tool, not a sandbox
for arbitrary firmware or a general AML dependency resolver.

```bash
python3 experiments/replay_acpi_d3_declaration.py /path/to/reviewed.dsl \
  --scope '\_SB.PCI0.RP01' --output /tmp/acpi-declaration.json
```

Four cases passed for the reviewed method:

| Modeled gate | Modeled helper | Result |
| --- | --- | --- |
| Off | Absent | `_DSD` not defined |
| Off | Present | `_DSD` not defined |
| On | Absent | Expected four-element package returned |
| On | Present | Expected six-element package returned |

These results show that real AML interpretation can distinguish dormant code
from a published declaration when its inputs are known. They do not prove the
live gate value. A local `Name (PDSD, Zero)` may be overwritten by an external
firmware/NVS value before the condition executes. Missing runtime inputs must
remain unresolved rather than being replaced with convenient defaults.

Before using this lead for recovery, resolve the actual gate and power-helper
implementations, identify every affected device, establish driver/IOMMU and
power-resource ownership, and verify a full reinitialization with a fresh workload.
The existing controller does not invoke root-port runtime suspend or arbitrary
ACPI methods based on this declaration.

Linux's [PCI ACPI reset implementation](https://github.com/torvalds/linux/blob/master/drivers/pci/pci-acpi.c)
uses the device's `_RST` method through the kernel reset path. That is distinct
from a `_DSD` declaration about what a D3-to-D0 transition does.

# Enterprise GPU hardware and recovery landscape

Reviewed 2026-10-15. This is a broad technical survey of NVIDIA GPU deployments,
not an exhaustive compatibility database or a list of physically recovered systems.
The companion [catalog](../references/enterprise-hardware-catalog.json) records
53 platform/configuration entries with sources, CPU architecture, GPU options,
form/topology classifications and explicit unknown firmware requirements. None
of these enterprise entries is physically recovery-qualified by this project.

The key finding is that GPU family and CPU architecture are insufficient to
choose a reset. The same accelerator family appears in PCIe cards, multi-GPU
baseboards, CPU-coherent modules and rack fabrics. Recovery needs a verified
control path and dependency domain for the actual installed system.

## Survey breadth and evidence levels

We examined NVIDIA hardware/management guides, OEM product and service guides,
CPU/GPU QVLs, firmware notes and cloud instance specifications. NVIDIA's current
[certification index](https://docs.nvidia.com/certification-programs/latest/nvidia-certified-systems.html)
contains 317 rows in its general data-center server table; its full set of
categories contains 742 rows, including reference configurations, edge, VDI and
workstations. The separate [previous-generation index](https://docs.nvidia.com/certification-programs/latest/nvidia-certified-systems-previous-generation.html)
contains 199 rows. These are overlapping published rows, not unique systems,
valid CPU/GPU Cartesian products, or reset tests. The indexes also point to
additional OEM/ODM suppliers beyond the representative systems below.

The read-only [index summary tool](../experiments/summarize_certified_systems.py)
retrieves both indexes and records HTML hashes, retrieval time and table counts.
Its [recorded result](../experiments/results/certification-index-breadth.json)
makes the breadth measurement reproducible without mirroring the vendor tables.
Certification establishes a tested system configuration, not recovery after Xid79.

Catalog entries distinguish historical documentation, vendor-documented options,
provider specifications, published platform designs and announcements. A published
user guide is not proof of general availability. Alternative CPU/GPU options
must not be multiplied together without applying the OEM restrictions. A null
BIOS, board or BMC version means unknown, never universal support.

## NVIDIA reference systems: documented combinations

| System / configuration | GPU arrangement | Host CPU | Connectivity distinction / source |
| --- | --- | --- | --- |
| DGX-1 Volta | 8 V100 SXM2 | 2 Xeon E5-2698 v4, x86 | NVLink cube mesh; historical [guide](https://docs.nvidia.com/dgx/archives/dgx1-user-guide/introduction-to-dgx1.html) |
| DGX A100 | 8 A100, 40GB or 80GB variants | 2 EPYC 7742, x86 | NVSwitch node [guide](https://docs.nvidia.com/dgx/dgxa100-user-guide/introduction-to-dgxa100.html) |
| DGX Station A100 | 4 A100 | EPYC 7742, x86 | Dedicated station [specifications](https://docs.nvidia.com/dgx/dgx-station-a100-user-guide/hardware-specifications-station-a100.html) |
| DGX H100 / H200 | 8 H100 or 8 H200 | 2 Xeon 8480C, x86 | NVSwitch node [guide](https://docs.nvidia.com/dgx/dgxh100-user-guide/introduction-to-dgxh100.html) |
| DGX B200 | 8 B200 | 2 Xeon 8570, x86 | GPU tray and NVLink switches [guide](https://docs.nvidia.com/dgx/dgxb200-user-guide/introduction-to-dgxb200.html) |
| DGX B300 | 8 B300 | 2 Xeon 6776P, x86 | AC/DC variants; PCIe Gen6 GPU–NIC links [guide](https://docs.nvidia.com/dgx/dgxb300-user-guide/introduction-to-dgxb300.html) |
| GH200 / GH200 NVL2 | 1 / 2 Hopper GPUs | 1 / 2 Grace, ARM | CPU–GPU coherent NVLink-C2C [guide](https://docs.nvidia.com/dccpu/grace-perf-tuning-guide/index.html) |
| DGX GB200 / GB300 NVL72 | 72 Blackwell GPUs per rack | 36 Grace, ARM | 18 compute trays; separate NVLink switch trays [guide](https://docs.nvidia.com/dgx/dgxgb200-user-guide/hardware.html) |
| DGX Station GB300 | 1 B300 | Grace, ARM | Coherent CPU–GPU module [software/platform guide](https://docs.nvidia.com/dgx/dgx-station-development-guide/porting/software-requirements.html) |
| DGX Vera Rubin NVL72 | 72 Rubin GPUs per rack | 36 Vera, ARM | Published design: 18 compute trays, 9 NVLink 6 switch trays, BMC/HMC [hardware guide](https://docs.nvidia.com/dgx/dgxvr-user-guide/hardware.html), [Vera architecture](https://www.nvidia.com/en-us/data-center/vera-cpu/) |
| DGX Rubin NVL8 | Rubin NVL8 platform | Xeon 6, x86 | CPU pairing announced; reset internals unqualified [Intel announcement](https://newsroom.intel.com/data-center/intel-xeon-6-used-as-host-cpus-in-nvidia-dgx-rubin-nvl8-systems) |

GB100 is a silicon-family identifier, while B200/B300 and GB200/GB300 identify
different product/platform contexts. Do not select a reset from a string prefix.
The repository's [Blackwell source review](enterprise-blackwell.md) covers pinned
HAL paths, not every product sharing those names. Likewise, Rubin's published
architecture does not establish a usable reset ABI or recovery support here.

## OEM servers: the chassis name is not enough

| Vendor / model | Documented NVIDIA configuration | CPU / material constraint | Source |
| --- | --- | --- | --- |
| Dell XE9680 | HGX A100, H100, H200 variants | Dual Xeon Gen4/Gen5; different GPU baseboards | [Service guide](https://www.dell.com/support/manuals/en-us/poweredge-xe9680/xe9680_ism_pub/dell-poweredge-xe9680-system-configurations-and-features?guid=guid-219a12b6-5232-49fe-9506-ac2aed25ac30&lang=en-us) |
| Dell XE9780 | 8 HGX B200 or B300 | Dual Xeon 6 6700P | [Portfolio specifications](https://www.dell.com/en-us/shop/servers/sf/poweredge-datacenter-servers?hve=explore+poweredge-cloud-scale-servers) |
| Dell XE9785 | 8 B300 variant | Dual EPYC 9005; thermal matrix applies | [Thermal matrix](https://www.dell.com/support/manuals/en-us/poweredge-xe9785/xe9785_ism/thermal-restriction-matrix?guid=guid-51aaf63a-9010-4987-9104-fa5965cb55c9&lang=en-us) |
| Lenovo SR680a V3 with B200 | 8 B200 SXM6 | Dual Xeon Gen5; separate B200 guide | [Product guide](https://lenovopress.lenovo.com/lp2247-thinksystem-sr680a-v3-with-b200) |
| Lenovo SR675 V3 | PCIe GPU shuttles or 4-GPU SXM configurations | EPYC 9004/9005; GPU/CPU/fan restrictions and withdrawn options | [Product guide](https://lenovopress.lenovo.com/lp1611-thinksystem-sr675-v3-server) |
| HPE XD685 | 8 H200, B200 or B300 variants | Dual EPYC Gen5; B300 requires high-power DLC chassis | [Current configurations](https://buy.hpe.com/us/en/compute/rack-servers/proliant-compute-xd600-servers/hpe-proliant-compute-xd685/p/1014862906), [QuickSpecs restrictions](https://www.hpe.com/kr/ko/collaterals/collateral.a00073553enw.html) |
| HPE XD690 | 8 B300 | Dual Xeon 6; air cooled | [Platform comparison](https://www.hpe.com/sg/en/products/compute/ai-servers.html) |
| Supermicro AS-8126GS-NB3RT | 8 HGX B300 | EPYC 9004/9005 per SKU; H14DSG-OM board | [System specifications](https://www.supermicro.com/en/support/manuals/product/system/as-8126gs-nb3rt/Content/appendix/system-specifications-as-8126gs-nb3rt.htm), [GPU specification](https://store.supermicro.com/us_en/8u-gpu-superserver-as-8126gs-nb3rt.html?options=cart) |
| Cisco C885A M8 H models | 8 HGX H100 or H200 | Dual EPYC 9004/9005; AMD-GPU M models are distinct | [Service guide](https://www.cisco.com/c/en/us/td/docs/unified_computing/ucs/c/hw/c885A/install/b_c885a-m8-server-hig/m_overview.html) |
| ASUS ESC N8-E11/E11V | 8 HGX H100/H200 | Xeon Gen5 platform; CPU-specific BIOS entries | [Platform](https://servers.asus.com/products/Servers/GPU-Servers/ESC-N8-E11/), [CPU/BIOS QVL](https://www.asus.com/networking-iot-servers/servers/gpu-servers/esc-n8-e11/helpdesk_cpu/?model2Name=ESC-N8-E11) |
| GIGABYTE G593-SD0-AAX1 | 8 HGX H100 SXM5 | Xeon Gen4/Gen5/CPU Max options; MSB3-G40 board | [Datasheet](https://download.gigabyte.com/FileList/DataSheet/G593-SD0-AAX1_datasheet_v1.2.pdf) |
| GIGABYTE G242-P33 | PCIe H100 / A100 options | Ampere Altra ARM; A100 QVL excludes Altra Max | [Rev.100 platform](https://www.gigabyte.com/eu/Enterprise/GPU-Server/G242-P33-rev-100), [QVL](https://download.gigabyte.com/FileList/QVL/server_qvl_G242-P33.pdf) |

These examples show why even a vendor product family is too broad a compatibility
key. For example, Lenovo restricts H200/RTX PRO field upgrades by CPU generation
and fan configuration. HPE assigns B200 and B300 to different power/cooling chassis
requirements. Such restrictions must be retained, not flattened into “supports
H200/B200/B300.” A NIC or NVMe device can also share a bridge with a GPU; schematic
or observed topology, not the chassis marketing diagram, determines reset scope.

The catalog adds Dell's Grace-based XE8712 NVL4 and XE9712 NVL72 configurations.
They belong with coherent modules/racks, not x86 HGX simply because they are Dell
servers. OEM management brands such as iDRAC, iLO, XClarity or Intersight are
entry points to investigate; their presence does not prove a GPU-scoped reset.

## POWER and ARM are not one alternate-architecture category

IBM's [AC922 documentation](https://www.redbooks.ibm.com/abstracts/redp5494.html)
describes two POWER9 processors and up to six V100 GPUs with NVLink. CPU–GPU
coherence changes the recovery boundary. IBM's historical
[firmware advisory](https://www.ibm.com/support/pages/ac922-firmware-8335-gtg-op91095)
explicitly connects a faulted GPU reset with a system termination risk when the
required driver level is missing. Its old version numbers apply to that historical
firmware combination, not a current universal recommendation.

IBM's [IC922](https://www.redbooks.ibm.com/abstracts/redp5584.html) instead supports
up to six T4 accelerators on POWER9. Neither system is qualified by the repository's
x86/ARM QEMU campaigns. No ppc64le recovery execution is claimed.

An Ampere Altra host with PCIe GPUs is different from Grace paired coherently with
a Hopper/Blackwell GPU, despite both using ARM. Vera adds another coherent-platform
generation. The GIGABYTE QVL even contains a GPU-generation labeling inconsistency:
its H100 row says “Ampere.” Treat model/PCI/subsystem identity and OEM confirmation
as evidence; do not turn catalog typos into generation-based reset rules.

## Cloud combinations and ownership

| Provider family | Published GPU / CPU combinations | Source |
| --- | --- | --- |
| AWS P4d/P4de | A100 / Xeon 8275CL | [Instance specification](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| AWS P5/P5e | H100/H200 / EPYC 7R13 | Same specification |
| AWS P5en | H200 / Xeon Sapphire Rapids | Same specification |
| AWS P6-B200/P6-B300 | B200/B300 / Xeon Emerald Rapids | Same specification |
| AWS P6e-GB200 | B200 / Grace ARM | Same specification |
| Azure ND A100 v4 | A100 / EPYC 7V12 | [ND family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family) |
| Azure ND H100 v5 | H100 / Xeon Sapphire Rapids | [Series specification](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series) |
| Google A2 / A3 High-Mega | A100 / Cascade Lake; H100 / Sapphire Rapids | [GPU specification](https://docs.cloud.google.com/compute/docs/gpus), [CPU platforms](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines) |
| Google A3 Ultra / A4 | H200 / B200, both Emerald Rapids | Same specifications |
| Google A4X / A4X Max | B200 / B300 with Grace ARM | Same specification |
| Google G2 / G4 | L4 / Cascade Lake; RTX PRO 6000 / EPYC Turin | Same specifications |

Cloud instance data does not disclose a complete motherboard/BIOS/control mapping.
A bare-metal offering also does not automatically grant BMC, power-shelf or
cross-tenant reset authority. The service must integrate the provider's supported
repair/replacement workflow and preserve ownership limits. A reset from inside a
VM cannot be assumed equivalent to host recovery. Region, instance revision and
provider-supported driver image are additional qualification dimensions.

## Reset mechanisms differ across generations and topology

| Mechanism / class | Where to investigate | Essential limitation |
| --- | --- | --- |
| NVML/driver reset | Responsive GPU and supported driver | Needs live transport and quiescent clients; not guaranteed physical recovery |
| Kernel FLR / device reset | Present PCIe endpoint with supported reset methods | Configuration access and driver lifecycle still matter |
| Secondary-bus / managed-switch hot reset | Surviving upstream bridge or independently managed port | Resets downstream scope; not proof of PERST or full power removal |
| ACPI reset / platform power transition | Firmware-declared, qualified resources | Depends on actual platform wiring, ownership and shared resources |
| NSM/BMC reset | Explicitly mapped supported GPU management endpoint | Protocol presence is not implemented ASIC reset authority |
| Independent full-power recovery | Qualified carrier or tray controls | All GPU supply/backfeed paths, sequencing and unrelated-device isolation required |
| Coherent module / rack recovery | Grace, Vera, POWER9 CPU–GPU links; partitioned NVSwitch | CPU memory, host and fabric dependencies may prevent GPU-only reset |

NVIDIA documents a material generation distinction: Ampere NVSwitch individual
GPU reset depends on Fabric Manager, while Hopper and later NVSwitch reset removes
that particular dependency. Pre-Ampere NVLink peers can require a joint reset.
This does not remove fabric revalidation or make a lost PCIe device reachable.
See [NVIDIA reset semantics](https://docs.nvidia.com/deploy/nvidia-smi/index.html)
and the [Fabric Manager guide](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/).

V100/Volta is also outside the supported range of NVIDIA's open kernel modules;
source observations from modern open-driver paths cannot be assumed to describe
its installed proprietary-driver lifecycle. NVIDIA states that distinction in
its [driver transition guidance](https://developer.nvidia.com/blog/nvidia-transitions-fully-towards-open-source-gpu-kernel-modules/).

## What a real qualification record must bind

A platform catalog is an input to bench selection. Runtime reset authorization
requires evidence for the installed machine:

1. Physical GPU UUID/serial, PCI and subsystem IDs, form factor, silicon/board
   revision, VBIOS/GSP/FSP identifiers and installed driver flavor/version.
2. Host vendor/model, motherboard and riser/baseboard revision, CPU SKU/socket
   topology, UEFI/SBIOS, BMC/HMC/CPLD/retimer/switch firmware, OS/kernel and IOMMU
   ownership. Null or unsupported versions must remain gaps.
3. Actual CPU–GPU, GPU–GPU and GPU–NIC paths; PCI bridge descendants; NVLink/NVSwitch
   partitions; coherent-memory dependencies; MIG/vGPU/VFIO ownership and all hosts
   affected by an independently controlled port or power domain.
4. A physically verified management endpoint, serial-to-port mapping, supported
   command, transport/power independence, assert/release semantics and durable
   ambiguous-operation reconciliation. “BMC present” is insufficient.
5. Dedicated-bench results for actual Xid79, absent endpoint, busy/blocked driver,
   lost control replies, repeated faults and management-controller restart. Check
   unrelated I/O, unchanged host boot, every returned GPU's identity, fresh CUDA,
   collectives/partitions and restored tenant ownership before releasing holds.

The existing prepared capsule binds boot, topology, kernel, driver version,
selected DMI/BIOS values and backend configuration. It does **not** capture and
attest every item above. Those missing dimensions still need a qualified platform
helper and acceptance record. Neither this catalog nor the offline power-domain
checker is a runtime allowlist.

## How this reduces the combination problem

The design direction is to qualify reset **contracts**, then reuse a common
orchestrator across configurations that satisfy the same contract. It is not
safe to enumerate CPU × motherboard × GPU names and assign a register sequence.

A useful engineering model is a dependency graph with distinct PCIe paths,
CPU–GPU coherence, NVLink partition membership, reset wiring, supply rails and
management reachability. A candidate action is admissible only when its affected
domain and dependency closure are owned and drained, its control path survives
the failure, and it preserves all resources outside the approved domain. Unknown
edges are unresolved evidence. Public product tables cannot reveal every edge.
This graph-based qualification model is a design recommendation, not an implemented
electrical-discovery engine or a proof of universal reset support.

The implemented [Switchtec adapter](switchtec-adapter.md) supplies one such explicit
managed-port contract. The [Redfish adapter](redfish-adapter.md) supplies another
for supported GPU Processor.Reset resources. The proposed
[independent controller](independent-recovery-controller.md) addresses removable
PCIe systems without suitable motherboard controls. Coherent modules and rack
platforms require their own documented lifecycle, including escalation when a
no-host-reboot reset is unavailable.

## Remaining coverage

This survey covers representative x86, ARM and POWER9 NVIDIA deployments and
current/previous certification indexes. It does not enumerate every ODM board,
regional accelerator variant, historical Tesla system, BIOS build, cloud host
revision, private OEM reset command or unannounced design. GPU families such as
A10/A16/A30/A40, T4, L4/L40/L40S, workstation/RTX PRO and regional variants require
SKU-specific qualification even where they appear in the wider certification
indexes. Consumer board combinations remain separately unqualified beyond the
existing RTX 3090/MS-7D25 evidence. AMD/Intel accelerators found in the same server
families use different driver/error systems; Xid79 is NVIDIA-specific.

No native RISC-V, additional POWER-generation, Rubin firmware or physical
enterprise recovery support is inferred from architectural similarity. Expanding
the inventory is feasible; proving those recovery contracts needs actual hardware
and supported platform documentation. Universal hardware recovery has not been
delivered by this research.

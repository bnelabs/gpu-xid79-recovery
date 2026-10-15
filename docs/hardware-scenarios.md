# Consumer and enterprise recovery scenarios

A portable controller needs several recovery paths. The useful dividing lines
are reset scope, management reachability, firmware support, and who owns the
device lifecycle. A product family name does not establish those properties.
The scenarios below describe candidate routes and gaps, not a hardware support
list. Physical recovery validation in this project remains limited to the
documented RTX 3090 platform adapter.

| Scenario | Candidate route | What still has to be established |
| --- | --- | --- |
| Headless desktop, one GPU on an isolated root port | Targeted NVIDIA reset, then exposed kernel reset; mapped PERST adapter if needed | Reset restores this fault state, driver can detach, CUDA works afterward |
| Desktop GPU driving the display | Same hardware possibilities | Graphics users and display ownership must be released; compute-client enumeration alone is insufficient |
| Laptop dGPU with firmware-controlled power | Kernel-listed ACPI reset or a validated platform power sequence | Firmware ownership, shared power domains, EC behavior, and actual device return |
| eGPU over Thunderbolt/USB4 | Driver-aware PCI reset; a platform-specific enclosure/tunnel lifecycle | Router-to-GPU mapping, other tunneled devices, independent enclosure power, device and resource return |
| PCIe GPU with NVMe below the same bridge | Targeted supported device reset, or narrower verified adapter | Resetting that entire bridge would also affect storage; generic bus fallback refuses |
| Two GPUs on separate root ports | Individually supported NVIDIA reset; validated per-device lifecycle backend | PCI separation alone does not establish absence of fabric or software dependencies; generic kernel fallback currently refuses multi-GPU |
| Data-center PCIe GPU with a BMC | NVIDIA reset or independently mapped NSM/BMC reset | Endpoint identity, supported command, transport survival, reset domain and driver lifecycle |
| Ampere NVSwitch system | NVIDIA/fabric-aware reset | Fabric Manager state and affected peers; no expansion to all GPUs by guessing |
| Hopper or later NVSwitch system | Generation-supported NVIDIA/fabric-aware reset | Driver/platform permissions and fabric health; lack of an FM reset dependency is not proof of recovery |
| MIG or vGPU deployment | Instance-aware, host-controlled lifecycle | Other tenants, instance recreation and ownership; generic kernel fallback refuses MIG/virtualized modes |
| Full GPU passthrough VM | Host/hypervisor-controlled device lifecycle | Guest reset permissions, VFIO ownership and supported reset domain; a guest cannot create a host-side power switch |
| Integrated/coherent CPU–GPU platform | Platform-specific NVIDIA and firmware mechanisms | PCIe add-in-card assumptions may not apply; generic GPIO/bridge recipes are not transferable |

NVIDIA documents GPU reset constraints for active users, NVLink peers, and
NVSwitch generations in [nvidia-smi](https://docs.nvidia.com/deploy/nvidia-smi/index.html).
See also the [Fabric Manager reset discussion](https://docs.nvidia.com/hgx-platforms/fabric-manager-user-guide/index.html)
and [MIG guide](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/getting-started-with-mig.html).
These constraints are not evidence that reset will fix every Xid79 condition.

Thunderbolt deauthorization tears down a PCIe tunnel rather than cycling every
power rail in an enclosure. The tunnel may include storage or other devices.
The [kernel USB4/Thunderbolt guide](https://docs.kernel.org/admin-guide/thunderbolt.html)
explains this lifecycle. This project does not automatically change authorization.

## Scope tests with real guest PCI topology

The QEMU lab now supports an emulated NVMe controller as the peer, rather than
assuming every neighbor is another test endpoint. It has no namespace or storage
workload in these tests. It also supports admitting two synthetic endpoints as
GPUs to exercise the controller's multi-GPU guard.

Recorded x86-64 results with QEMU 10.2.1 and Linux 7.0.0-38:

| Layout | Result |
| --- | --- |
| Synthetic target and NVMe on separate bridges | Target counter reset; NVMe identity remained accessible |
| Synthetic target and NVMe on a shared bridge | Recovery refused; no sysfs writes; target counter unchanged |
| Two admitted synthetic GPUs on separate bridges | Multi-GPU fallback refused; no sysfs writes |

The shared-NVMe refusal also passed on ARM64 Linux 6.12.107. All cases preserved
the guest boot ID. These test scope enforcement, not GPU firmware, fabric,
storage integrity, or real NVIDIA recovery.

```bash
python3 experiments/qemu/run.py --scope-peer nvme --kernel /path/to/vmlinuz \
  --output /tmp/nvme-isolated.json
python3 experiments/qemu/run.py --scope-peer nvme --shared --kernel /path/to/vmlinuz \
  --output /tmp/nvme-shared.json
python3 experiments/qemu/run.py --scope-peer gpu --kernel /path/to/vmlinuz \
  --output /tmp/dual-gpu.json
```

The [lab instructions](../experiments/qemu/README.md) also cover ARM64 userspace.
QEMU's [NVMe documentation](https://www.qemu.org/docs/master/system/devices/nvme.html)
describes the emulated controller. Synthetic GPU admission, the nvidia-smi stub,
and final counter verification remain explicit substitutions.

## What the explorer now explains

`kernel_fallback_assessment` reports the current GPU count, blockers from the
existing multi-GPU/fabric/MIG/virtualization guards, shared PCI scope, unsupported
driver ownership, and reset-path selection errors. It also identifies unavailable
NVIDIA fields. A report without listed blockers does not establish hardware
support or authorize a reset. Runtime users, timeouts, and final verification
remain separate checks. A configured lifecycle backend is assessed separately;
this field describes generic kernel fallback only.

## Toward broader recovery coverage

Before failure, a platform can establish GPU identity, reset domains and an
independent control path. After failure, a controller can choose that verified
path, invalidate old workloads, restore the device, and run a fresh workload
check. This is a direction for extending adapters, not a new universal backend.
NVML's [drain/remove/discover APIs](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlZPI.html)
help manage driver visibility alongside external power/reset tools; they do not
supply missing physical connectivity.

An emulator can test orchestration and refusal cases. It cannot establish hidden
board wiring or firmware behavior that was never modeled or measured. No single
no-reboot mechanism covering every scenario above has been established here.

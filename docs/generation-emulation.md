# What generation emulation can establish

The service needs qualified recovery capabilities, not a guessed GPU generation
allowlist. A reset command returning success is insufficient. The transport must
survive the fault, the reset scope must be exclusively owned, and every affected
GPU must pass physical identity, workload and fabric validation before release.

Current evidence has distinct boundaries:

| Layer | Executed here | What remains unverified |
| --- | --- | --- |
| Service and OS | 74 isolated x86/ARM QEMU cases with synthetic PCI devices | Real GPU reset circuitry, NVIDIA driver and firmware |
| Driver function control flow | 11 actual upstream TU102/GH100/GB100 FLR function cases under ASan/UBSan | HAL dispatch, complete driver, register semantics and electrical effects |
| Workload timing | 18 GPEmu timing cases across six supplied profiles | PCIe, GSP, VBIOS, NVLink and hardware recovery |
| Physical platform | Site-specific adapters and qualification contracts | General qualification across boards, BIOS versions, trays and interconnects |

## Reproduce the generation replay

```sh
python3 experiments/replay_generation_flr.py /path/to/open-gpu-kernel-modules \
  --output /tmp/generation-flr.json
```

The checkout must contain the exact hashed inputs listed in
[the result](../experiments/results/generation-flr.json). The replay extracts
unmodified function bodies from NVIDIA commit
[ca2d03e](https://github.com/NVIDIA/open-gpu-kernel-modules/tree/ca2d03e6fafd4d4b56b383497d931c25b283bfcf).
Only transport, register fields and surrounding types are modeled. It does not
load NVIDIA modules or open physical devices.

The TU102 function performs its config read and write without propagating a
transport status at this function boundary. GH100 and GB100 return explicit read
and write errors; a failed read prevents a write. Both still return success when
our modeled transport reports success but supplies an all-ones value. TU102 also
returns success for the modeled lost-device read and failed write. These are
observations of these selected function bodies, not claims that complete upstream
drivers lack caller-side validation or apply these functions to every GPU.

The useful consequence is to retain independent post-reset verification and
quarantine on uncertainty. Do not turn this experiment into a raw configuration
write tool or bypass upstream generation gates.

## Generation coverage boundaries

| Generation | Available approach | Honest limit |
| --- | --- | --- |
| Kepler/Maxwell/Pascal | Supplied GPEmu workload profiles for some products; platform fault models | No faithful device or proprietary firmware emulator here |
| Volta | V100 workload profile; PCIe versus POWER9 NVLink platform qualification | Open KMD support begins at Turing; this replay does not validate V100 |
| Turing | Actual TU102 FLR function replay | No automatic applicability to all Turing boards |
| Ampere/Ada | Platform orchestration tests; upstream source available for further bounded replays | No complete GPU/GSP emulator or generation-wide physical qualification |
| Hopper | Actual GH100 FLR function replay | SXM/PCIe, NVSwitch, ownership and platform reset paths still differ |
| Blackwell | Actual GB100 FLR function replay and separate config-wait replay script | No inference of B200/B300/Grace platform electrical equivalence |
| Rubin | Published platform information and source/header inspection | No Rubin device/firmware emulator or recovery qualification here |

Changing a synthetic PCI device ID does not add GPU registers, firmware or reset
behavior. Running extracted firmware on a CPU emulator would additionally require
its actual instruction set, memory map, interrupts, peripheral models and boot
contracts. Successful instruction execution would still not establish that power
rails, PCIe links or coherent fabrics recover on physical hardware.

For consumer removable PCIe cards, the independent-controller proposal is a
managed carrier/interposer qualified for power, reset and backfeed. For enterprise
trays and coherent CPU/GPU systems, use documented platform management and the
platform's complete affected domain. Neither supplies a universal recovery wire
through software alone. See [the controller contract](independent-recovery-controller.md)
and [hardware landscape](enterprise-hardware-landscape.md).

# Enterprise recovery emulation

The enterprise lab exercises the production controller, local-domain orchestrator
and optional Redfish adapter inside disposable x86-64 and ARM64 QEMU guests.
Eight synthetic GPU endpoints sit in four two-device PCI reset groups, alongside
an unrelated NVMe controller. No host device, host directory or external network
is exposed to the guest. QEMU uses software CPU emulation.

This is enterprise orchestration emulation, not emulation of a functioning H100,
B200, NVSwitch or GSP firmware image. NVIDIA GPU identities and workload checks
are substituted; no NVIDIA firmware or CUDA workload executes.

## Components that actually execute

- Unchanged production `gpu_recovery.py` and `domain_adapter.py`, including scope
  checks, locks, trusted paths, subprocess handling and persistent service holds.
- The production `adapters/redfish_gpu.py` as separate subprocesses for preflight,
  recovery and final validation.
- A loopback HTTP BMC fixture that advertises GPU Processor.Reset and async
  TaskService endpoints. Accepted resets write real guest-kernel
  `reset_subordinate` interfaces. A paired sibling may reset too, which is inside
  the fully approved domain.
- Real QEMU PCI testdev counters, observed before and after kernel resets, and
  a guest NVMe controller outside the approved domain.

The `systemctl` executable is a file-backed guest shim. It records stop/start
requests and service active state; the actual production hold ledger persists
on the guest filesystem. It does not run real systemd or application workloads.
The fixture's lifecycle helper checks an independent synthetic membership map,
actual guest reset scope and modeled service state, then supplies nonce-bound
receipts. Fabric failure is an injected receipt result, not an NVLink model.
Counters replace per-GPU NVML/CUDA validation. The real systemd/CUDA validation
previously documented in `service-lifecycle.md` is separate evidence.

## Run

Use the same prerequisites as the [base QEMU lab](../experiments/qemu/README.md).

```bash
python3 experiments/qemu/run_enterprise.py \
  --kernel /path/to/x86-bzImage --output /tmp/enterprise-x86.json

python3 experiments/qemu/prepare_arm64.py /tmp/arm64-reset-lab
python3 experiments/qemu/run_enterprise.py --arch aarch64 \
  --kernel /tmp/arm64-reset-lab/linux --userspace /tmp/arm64-reset-lab/root \
  --output /tmp/enterprise-arm.json
```

Add `--storage-shared` for a separate refusal test: NVMe is placed inside one
proposed GPU reset group. The production domain inventory must reject it before
any management reset. ARM64's pinned input manifest now includes `libzstd1`,
required by the recorded OpenSSL dependency used by Python's SSL module. Without
that dependency the adapter cannot import, which is not a valid injected-fault
result. Fault cases require their expected failure reason and phase evidence.

Each run writes JSON plus a console log. JSON records kernel and executable
input hashes, GPU counts, reset POST counts, service starts, hold records,
verified members, unchanged boot ID and unrelated NVMe configuration checks.
The runner fails if any case fails and has a 300-second guest-process bound.
Its initial root construction time is outside that bound. The guest-only fixture
refuses to execute without its explicit disposable-kernel marker and exact
QEMU testdev identities.

## Campaign and expected outcomes

| Case | Expected observation |
| --- | --- |
| Successful domain recovery | Eight reset POSTs; all eight counter checks and final fabric receipt pass before any of nine services starts |
| Wrong serial / CPU resource / node reset action / unsupported restart | No reset POST; GPU counters unchanged; services held |
| Lifecycle ownership denied | No reset POST; services held |
| Partial reset | Stop at third POST; no reset retry or generic fallback; all services held |
| Failed task / task timeout / lost reset response | One POST only; stop without resetting another member; services held |
| Last member workload failure | Eight POSTs and seven successful member checks; no service starts |
| Failed fabric validation / stale validation nonce | All eight member checks complete; final domain validation refuses release |
| Resume after external restoration | Persistent holds released only after whole-domain verification; zero reset POSTs |
| NVMe in proposed reset scope | Scope refusal before any reset; seeded counters unchanged |

There are 14 normal-layout cases and one separate shared-storage case per
architecture. All tests also require boot continuity and unchanged unrelated
NVMe identity/configuration. NVMe namespace I/O is not tested: this fixture has no
backing storage namespace. Missing physical endpoints, actual broken PCI links,
NVIDIA driver teardown and BMC transport surviving a real fault remain outside
this campaign.

## Recorded results

Recorded on October 15, 2026 with QEMU 10.2.1:

| Guest | Kernel | Normal campaign | Shared-storage refusal |
| --- | --- | --- | --- |
| x86-64 Q35 | 7.0.0-38-generic | 14/14 passed | 1/1 passed |
| ARM64 virt | 6.12.107+deb13-arm64 | 14/14 passed | 1/1 passed |

Results are stored under [experiments/results](../experiments/results/).
Use their input hashes to identify the tested source; later modifications require
a fresh campaign. Initial development failures (loopback not brought up and a
missing ARM64 SSL dependency) were corrected before the recorded acceptance runs.

A separate [NVIDIA sequencer replay](out-of-band-reset.md#optional-redfish-transport-and-sequencer-fault-replay)
compiles the unchanged upstream asynchronous NSM reset method with modeled
command results and checks 512 outcome combinations. That supplies source-level
evidence about status handling and assertion/release sequencing, not firmware
execution or physical reset success.

## What remains for real deployment

The adapter needs provisioned BMC-to-GPU identities, a working advertised reset,
authority over its complete physical scope and a real lifecycle helper. MIG,
virtualization, tenant draining, Fabric Manager/NVSwitch partition restoration,
driver detach/rebind and topology-specific BMC implementations still need
platform code and physical tests. A declared receipt cannot establish the truth
of those claims by itself.

Full proprietary GPU firmware emulation would additionally require working
models of boot ROMs, privileged engines, PCIe behavior, memory controllers and
board reset circuitry. Replaying isolated source methods or booting a generic
RISC-V emulator does not supply those models. The service retains holds and
escalates when it lacks a verified recovery path.

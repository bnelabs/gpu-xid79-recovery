# Enterprise acceptance evidence

Recorded on October 15, 2026. All reports contain synthetic identities only.

- `enterprise-x86_64.json`: 23-case normal-layout campaign, x86-64 Q35.
- `enterprise-aarch64.json`: 23-case normal-layout campaign, ARM64 virt.
- `enterprise-storage-x86_64.json` and `enterprise-storage-aarch64.json`: NVMe
  shares a proposed GPU reset group; scope refusal is the required result.
- `nsm-lifecycle.json`: 512 modeled outcome cases through the unchanged upstream
  asynchronous NSM reset sequencer, compiled with ASan/UBSan.

The four QEMU reports passed, and their `inputs_sha256` were checked against the
working files after all acceptance runs. Kernel hashes and QEMU versions are
included. These are file-level input pins, not a claim of physical GPU support.
Normal-layout campaigns also require unchanged unrelated NVMe PCI configuration;
namespace storage I/O is not tested. See [the full methodology](../../docs/enterprise-emulation.md).

The companion Python suite passed 155 tests. No host GPU reset, BMC connection,
production workload interruption, firmware flashing, or service enablement was
performed in this work.

The current campaign additionally checks restart-persistent unresolved reset
records, externally restored whole-domain resume without another reset POST,
and failed reconciliation retaining both the journal and workload holds. Normal
cases use separate journal state to isolate injections; the restart/reconciliation
cases intentionally preserve state across fresh adapter subprocesses. These are
not tests of a real host reboot or management-controller restart.

Prepared-domain cases now remove two or all eight endpoints before recovery.
The reports record and assert the exact `initially_missing_functions`, verify
all eight returned members, and require all nine services to start only after
whole-domain validation. Additional failures cover expired records, changed
saved parents, missing independent-control confirmation and wrong post-return
UUIDs. These remain logical removals of electrically present QEMU test devices.

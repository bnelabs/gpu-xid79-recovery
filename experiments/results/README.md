# Enterprise acceptance evidence

Recorded on October 15, 2026. All reports contain synthetic identities only.

- `enterprise-x86_64.json`: 14-case normal-layout campaign, x86-64 Q35.
- `enterprise-aarch64.json`: 14-case normal-layout campaign, ARM64 virt.
- `enterprise-storage-x86_64.json` and `enterprise-storage-aarch64.json`: NVMe
  shares a proposed GPU reset group; scope refusal is the required result.
- `nsm-lifecycle.json`: 512 modeled outcome cases through the unchanged upstream
  asynchronous NSM reset sequencer, compiled with ASan/UBSan.

The four QEMU reports passed, and their `inputs_sha256` were checked against the
working files after all acceptance runs. Kernel hashes and QEMU versions are
included. These are file-level input pins, not a claim of physical GPU support.
Normal-layout campaigns also require unchanged unrelated NVMe PCI configuration;
namespace storage I/O is not tested. See [the full methodology](../../docs/enterprise-emulation.md).

The companion Python suite passed 126 tests. No host GPU reset, BMC connection,
production workload interruption, firmware flashing, or service enablement was
performed in this work.

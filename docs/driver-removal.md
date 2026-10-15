# Driver removal and the limits of emulation

Recovery needs both a reachable hardware reset and a way to discard old driver
state. A display queue that never completes, a blocked UVM cleanup operation,
and a disconnected ASIC require different handling.

The [experimental teardown patch](../patches/README.md) covers selected display
waiters and removal paths. Its CPU models do not validate callback lifetime,
kernel lock ordering, active UVM cleanup, DMA quiescence, or fresh driver
initialization. It has not been loaded on hardware here.

## Available emulator coverage

`apnex/fake-5090` proposes a vfio-user device for NVIDIA enumeration and failure
testing. At reviewed commit `8f3777bac3ea8fad65989354a93c9dc466ba4398`, its status
says it is a survey with no implementation. It cannot currently run our driver
candidate. Its proposed scope also excludes compute and CUDA. See [the project
status](https://github.com/apnex/fake-5090/blob/8f3777bac3ea8fad65989354a93c9dc466ba4398/README.md).

Our QEMU experiments run real Linux PCI paths with synthetic devices. They do
not initialize working NVIDIA RM/GSP/UVM state, so they cannot validate teardown
of that state after failure.

## A failure without PCIe link loss

[NVIDIA issue 1333](https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1333)
reports H100 NVL UVM cleanup followed by blocked NVML calls, without Xid events
or loss of PCI enumeration. That report has not been reproduced here. It adds a
distinct test case beyond disconnected PCIe and stalled display queues.

The reviewed `nvidia_dev_put_uuid` calls `nv_close_device`, updates the external
client count, then releases `ldata_lock`. Its warning statement does not itself
return before that release. This does not exclude blocking or lock errors in
called functions, nor establish the reported root cause. Removing the warning
would not implement recovery. See [current source](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/kernel-open/nvidia/nv.c).

## Remaining tests

| Failure state | Required validation |
| --- | --- |
| Display waiter holding the modeset lock | Actual lost-event delivery and bounded waiter exit |
| Callback racing device destruction | Cancellation, synchronization of in-flight callbacks and object lifetime |
| Active CUDA/UVM clients | Context invalidation, DMA cessation and cleanup without orphaned locks |
| Reachable PCI function with dead firmware | Supported ASIC reset and fresh RM/GSP initialization |
| Inaccessible endpoint | Surviving upstream or independent control, then driver recreation |
| Fabric or partitioned deployment | Peer/tenant ownership and restoration of the affected domain |

No teardown patch has been validated across all these cases here. The generic
controller stops after a timeout. This bounds its own attempt; it cannot release
a stuck kernel lock or create a missing physical reset path.

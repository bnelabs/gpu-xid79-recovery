# Recovery through a management controller

An independent management controller can provide a reset route when the host
cannot reach the GPU over PCIe. This can avoid a host-side GPIO mapping on
platforms that expose the required operation. Installing a management daemon
does not create that hardware connection.

## NVIDIA NSM fundamental reset

NVIDIA's `nsmd` source contains a GPU reset handler backed by NSM PCI-link
command `0x60`. It sends an assertion followed by a release. The handler obtains
the management endpoint from an NSM device and the device index from platform
configuration. That configured index is not a Linux PCI bus address.

This is a concrete candidate for an enterprise backend. It is not evidence that
GeForce cards implement the command. Before using it, a backend needs to establish:

* Which management endpoint and device index identify the physical GPU.
* Whether that endpoint advertises the PCI-link command and accepts the operation.
* Whether the transport remains reachable when the GPU's host PCIe link fails.
* Which other devices share its reset domain, including NVLink dependencies.
* How driver removal, reset release, re-enumeration, and CUDA verification work.

The [OpenSMA receiver review](management-firmware.md) found a default reset hook
that returns NotSupported and a target-index list for other platform devices.
NSM command encoding alone does not establish receiver support or a GPU mapping.

The source's asynchronous GPU path performs both assertion and release; its
synchronous `reset()` implementation returns without sending either command.
A backend must use the actual asynchronous operation and check its result.
An ambiguous timeout after assertion also needs reconciliation: losing a reply
does not tell us whether reset was asserted. A successful protocol reply alone
does not establish restored PCIe access or functioning CUDA.

Reviewed upstream revision: `b6cddce5ddab2afeed95142da608ed5865db96f2`.
Sources: [GPU reset configuration](https://github.com/NVIDIA/nsmd/blob/b6cddce5ddab2afeed95142da608ed5865db96f2/nsmd/nsmProcessor/nsmReset.cpp),
[asynchronous reset implementation](https://github.com/NVIDIA/nsmd/blob/b6cddce5ddab2afeed95142da608ed5865db96f2/nsmd/nsmDbusIfaceOverride/nsmResetIface.hpp),
[command encoding](https://github.com/NVIDIA/nsmd/blob/b6cddce5ddab2afeed95142da608ed5865db96f2/libnsm/pci-links.c).

## Redfish, PLDM, and MCTP

Redfish defines `Processor.Reset` and `Port.ResetPPB`. A GPU may be represented
as a Processor, but a reset action on a CPU, whole system, or chassis has a
different scope. Discover resource identity, advertised actions, and supported
parameters; do not invent a GPU reset URI from its PCI address. A bridge reset
can affect every device below the bridge. See the DMTF
[Processor schema](https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/json-schema/Processor.v1_24_0.json)
and [Port schema](https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/json-schema/Port.v1_18_0.json).

MCTP is a transport, not a promise of reset support. NVIDIA's PCIe-card
management guide describes NSM telemetry over USB/MCTP, PLDM firmware updates,
and I²C firmware recovery. Those capabilities must not be treated as interchangeable
runtime GPU reset mechanisms. A GPU-hosted I²C controller is also not independent
of the GPU's PCIe connection. See [NVIDIA's management guide](https://github.com/NVIDIA/nvbmc-docs/blob/develop/nvidia/Platforms/gpu-pcie-card-mgmt.md).

## Reproduce the investigation

Read existing local management inventory without sending device commands:

```bash
python3 experiments/inspect_management.py --output /tmp/management-inventory.json
```

This checks GPU-relative I²C adapters, NVIDIA USB candidates, MCTP links, IPMI
device nodes, SMBIOS type 42 record names, and relevant local D-Bus services.
Empty results do not exclude an externally connected BMC.

Compile and exercise the reviewed upstream codec without opening any transport:

```bash
git clone https://github.com/NVIDIA/nsmd.git /tmp/nsmd
git -C /tmp/nsmd checkout b6cddce5ddab2afeed95142da608ed5865db96f2
python3 experiments/replay_nsm_reset.py /tmp/nsmd --output /tmp/nsm-codec.json
```

Requires a C compiler with AddressSanitizer and UndefinedBehaviorSanitizer.
The replay checks source hashes before compilation. Recorded results: 512 valid
request round trips, nine truncated lengths rejected, capability bitmap decoding,
and success/unsupported-command responses passed. The codec also accepts action
values outside 0 and 1; callers need to enforce that range themselves.

These tools do not send a reset. No NSM hardware recovery has been validated in
this project. The default installer now copies the optional Redfish adapter, but
never selects or enables it; there is still no integrated NSM transport backend.

A separate [managed-switch review](switch-management.md) covers per-port reset/retrain controls and management transport requirements.

## Optional Redfish transport and sequencer fault replay

The optional [Redfish GPU adapter](redfish-adapter.md) now implements scoped
Processor.Reset requests under the local-domain contract. It still requires a
real platform lifecycle helper. No automatic NSM backend or physical enterprise
recovery is established by this addition.

The new sequencer replay extracts the unchanged `NsmResetAsyncIntf` method from
the pinned source above and compiles it with ASan/UBSan and a minimal coroutine
harness. It tests every byte-valued assertion result and every byte-valued release
result (512 cases). The modeled command transport completes immediately; no
actual firmware or asynchronous transport scheduling is emulated.

```bash
python3 experiments/replay_nsm_lifecycle.py /path/to/nsmd --output /tmp/nsm-lifecycle.json
```

All 512 cases matched the source's status behavior. The coroutine returns
`NSM_SW_SUCCESS` even when the async status becomes `InternalFailure`. A caller
must consume the asynchronous status object. Also, a failed assertion result
never leads to a release request. If assertion physically occurred but its reply
was lost, this code alone does not reconcile or release that state. A future
transport backend needs platform-specific reconciliation; blindly replaying or
claiming success from a coroutine return value is insufficient.

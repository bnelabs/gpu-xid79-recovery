# Firmware recovery investigation

This is an initial offline binary triage and source-correlation result, not a
completed firmware reverse engineering effort or a demonstrated recovery exploit.
No firmware was executed, flashed, patched or uploaded to Lumina. The production
GPU was not accessed. Existing uncommitted work in the private firmware lab was
left untouched.

## Reproducible binary evidence

`experiments/inspect_gsp_firmware.py` inventories ELF64 containers, hashes inputs,
indexes bounded embedded RISC-V program-file ranges, and records recovery-related
ASCII strings with payload and outer-file offsets. The
[report](../experiments/results/gsp-firmware-triage.json) contains no firmware
binaries or private host captures.

| Driver image | Embedded ELF program-range candidates | Recovery string candidates |
| --- | ---: | ---: |
| 535.113.01 | 4 | 49 |
| 570.144 | 8 | 51 |
| 615.71.09 | 12 | 66 |

These are structural/search candidates, not counts of recovered handlers. None of
the candidates passed complete section-table validation. For example, the first
615 ELF header at payload `0x6a000` advertises a section table at relative
`0x2d0d0`, but the referenced entries are not a valid named-section table. The
program headers are separately bounded. Blindly slicing between ELF magic values
and feeding the result to a decompiler could mislead analysis. Archive decoding
and load-address reconstruction are required before trustworthy cross references.
Signature names and nearby `kernel_*.elf` names do not establish ownership of
these program ranges.

Reproduce on local copies, passing all three image paths:

```sh
python3 experiments/inspect_gsp_firmware.py /path/gsp-535.113.01.bin \
  /path/gsp-570.144.bin /path/gsp_ga10x.bin --output /tmp/triage.json
```

## Leads and transport requirements

1. **Reset-failure classification rather than blind retry.** The 615 payload
   contains `PCIE_RESET_FAILURE: Classification: %d, Recommendation: Perform SBR`
   at payload `0x1041928` (outer-file `0x1041968`). The exact diagnostic appears in
   [`_kbifReportResetStatus_GR100`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/bif/arch/rubin/kernel_bif_gr100.c).
   The reviewed source classifies illegal acknowledgement and several reset
   assertion/deassertion timeouts, logs Xid 182 and marks the GPU for reset.
   Unclassified cases log an unknown recommendation. The diagnostic match is
   source correlation; we have not recovered its firmware call graph or proved
   which embedded architecture executes it. It does not execute SBR itself.
   A future decoder could preserve this evidence for an operator or qualified
   adapter, but must not expand reset scope from a string recommendation.

2. **GSP restart when host register access survives.**
   [`kgspResetHw_GH100`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/gsp/arch/hopper/kernel_gsp_gh100.c)
   writes the GSP engine reset field and polls assertion/deassertion state.
   [`kgspResetHw_GB100`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/gsp/arch/blackwell/kernel_gsp_gb100.c)
   first waits for read/write protection to permit access, then delegates to the
   GH100 routine. This is a candidate for driver-mediated recovery of a wedged
   microcontroller while the register transport remains available, not an
   independent way into a disconnected GPU. A production path additionally needs
   supported teardown, DMA/client quiescence, bootstrap, identity and workload
   validation; no direct register-write adapter is provided.

3. **Reset policy vocabulary is not a new actuator.** `RMSecBusResetEnable`
   occurs in all three images. The upstream registry header calls its generic
   default false, while Unix initialization explicitly enables it. Merely setting
   this key is therefore not a discovery of a disabled universal reset path.
   `FULL CHIP RESET`, `BUS RESET`, `GC6 RESET` and `UCODE RESET` labels likewise
   describe vocabulary, not independently callable commands.

4. **Firmware RPC recovery remains conditional on the channel.** The documented
   [GSP command/status queues](https://docs.kernel.org/gpu/nouveau.html#gsp-support)
   carry RPC requests and responses. A discovered handler still needs a working
   channel and boot/security state. For a dead GSP or lost host path, the useful
   search shifts to an independent management controller, reset signal or power
   domain. Such a path must be established per platform rather than assumed from
   a GSP string.

## Next experiments justified by the evidence

Decode archive descriptors using the loader/layout source before assigning ELF
program ranges to named kernels. Reconstruct load mappings, then use IDA or a
RISC-V disassembler to recover references to the reset-classification diagnostics
and distinguish logs, classifiers and actuators. Compare the same routine across
versions rather than interpreting whole-image byte differences.

On a disposable board, separately inject (a) RPC hang with live PCIe, (b) partial
register/config access and (c) complete link loss. Capture which transports remain
usable and whether the documented driver path can bootstrap safely. Only then can
an adapter claim a supported recovery mode. No physical fault injection or
complete firmware disassembly was performed in this initial pass.

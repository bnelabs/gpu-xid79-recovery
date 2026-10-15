# Can a GSP restart be a general recovery backend?

The current answer is **not from the reset function's status alone**. We executed
actual upstream reset bodies under prescribed failure outcomes before considering
an adapter. The [14-case result](../experiments/results/gsp-reset.json) exposes
important limits to the candidate identified during firmware triage.

```sh
python3 experiments/replay_gsp_reset.py /path/to/open-gpu-kernel-modules \
  --output /tmp/gsp-reset.json
```

The script checks exact source hashes and extracts the unmodified bodies of
`kgspResetHw_TU102`, `kgspResetHw_GH100` and `kgspResetHw_GB100` from commit
`ca2d03e6fafd4d4b56b383497d931c25b283bfcf`. It compiles a temporary C executable
with AddressSanitizer and UndefinedBehaviorSanitizer. It never loads a module,
opens GPU devices or writes hardware. Register operations, timeout outcomes and
surrounding types are models; polling predicates and complete driver callers are
not executed.

## Results

| Actual function | Injected condition | Observed local control flow |
| --- | --- | --- |
| TU102 | Propagation reads return all ones | Returns success after two modeled writes and 20 reads; read values do not determine status |
| GH100 | Assertion wait fails, deassertion wait succeeds | Logs/breakpoint, continues to deassert, returns success |
| GH100 | Deassertion wait fails | Returns final wait error |
| GB100 | Reset-access wait fails | Logs/breakpoint, still delegates to GH100 |
| GB100 | Access and/or assertion waits fail, deassertion succeeds | Returns success under the modeled non-halting breakpoint behavior |

All four assertion/deassertion success/failure combinations and all eight
access/assertion/deassertion combinations were executed. Two TU102 cases compare
ordinary and all-ones modeled reads. Each case asserts reset write ordering,
wait/timeout counts and breakpoint counts, not just return status.

The breakpoint assumption is explicit: upstream `core/printf.h` permits a
non-halting release definition of `DBG_BREAKPOINT()`. Debug/QA/LibOS configurations
can behave differently. These results do not prove the installed proprietary
module has a particular build configuration, that hardware accepts writes through
a closed protection mask, or that a full driver boot would succeed. Continuing to
attempt a write is not a demonstrated privilege bypass.

## Generation applicability checked in source

The generated dispatch at
[`g_kernel_gsp_nvoc.c`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/generated/g_kernel_gsp_nvoc.c)
selects the TU102 implementation for its explicit Turing, GA100/GA10x and AD10x
chip cases, GH100 for its explicit GH100 case, separate stubs for VF and listed
Tegra cases, and GB100 in the remaining branch. That dispatch was inspected,
not executed by this replay. It explains why function names alone are an
incomplete generation map. It does not qualify every product matching a family.

Reviewed generated-file SHA256:
`3efc3c328576587dd34924829c9d607ad8515f9ca455ee5ce1e4d6ffafb45673`.
The TU102 propagation count of ten is taken from `gpu/falcon/falcon_common.h`.

## Bootstrap still needs an intact supported lifecycle

In
[`kgspBootstrap_GH100`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/src/nvidia/src/kernel/gpu/gsp/arch/hopper/kernel_gsp_gh100.c),
boot can proceed through FSP, SEC2, or the local FMC bootstrap routine depending
on device properties. The local routine calls the reset HAL, then programs FMC
arguments and code/data/manifest DMA addresses. Other branches submit boot
commands through their controller. The routine also deals with protected memory,
confidential-computing state and target-mask release.

Consequently, directly copying a reset-field write into a userspace helper would
omit required ownership, memory, bootstrap and device-state handling. It would
also depend on the very register transport that a fully disconnected GPU lacks.
No such helper is added to the service.

## Concrete engineering directions

For a future driver-integrated recovery path, retain every reset-stage outcome
rather than interpreting the final wait alone as recovery. If assertion fails,
deassertion may still be necessary cleanup; returning early without an approved
hardware sequence is not automatically correct. Report cleanup separately from
whether a reset was established. Bootstrap, fresh GSP initialization and workload
validation must be separately established before returning a GPU to service.
Any changed upstream error policy needs vendor/hardware validation; this project
has not patched or installed NVIDIA's driver.

For the existing service, continue using explicit domain ownership, independent
health receipts, durable unresolved-reset tracking and post-reset workload/fabric
checks. A hung GSP with live register access and a completely missing PCIe endpoint
are different fault classes. The former may eventually support a qualified
driver lifecycle backend. The latter needs an actuator whose transport survives
GPU loss: a documented platform controller or qualified independent hardware.

The next physical experiment requires a disposable test GPU and a supported
instrumented driver: capture stage outcomes during a GSP-only fault, perform the
complete teardown/bootstrap lifecycle, and validate all affected clients and
fabric state. This replay supplies a fault matrix for that experiment; it does
not substitute for it.

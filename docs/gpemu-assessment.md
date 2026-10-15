# GPEmu assessment for GPU recovery testing

Reviewed [mengwanguc/gpemu](https://github.com/mengwanguc/gpemu) at revision
`27e9534ee0c3d594030e2519d4a418b3f0a7860f`. It is useful for the workload side
of recovery experiments. It is not an emulator of NVIDIA device firmware or
PCIe reset behavior.

## What the implementation models

The [timing library](https://github.com/mengwanguc/gpemu/blob/27e9534ee0c3d594030e2519d4a418b3f0a7860f/gpemu/gpemu/gpemu/emulators.py)
implements forward/backward compute and transfers with synchronous/asynchronous
sleep calls. Memory checks compare profile values against a capacity; preprocessing
can generate CPU load. These mechanisms model resource demand and scheduling,
not GPU numerical execution, register state or firmware initialization.

The included profile directories cover A100-SXM4-40GB, V100-PCIE-32GB, P100,
M40, K80 and Quadro RTX 6000. They do not supply H100, H200, Blackwell or Rubin
firmware models. User-selected timings would be hypotheses for those devices.
The regression fallback references `joblib` and `np` without importing them in
this module; an unprofiled-batch check raises `NameError: joblib` at this revision.
Do not treat interpolation or multi-GPU scaling as qualified by our check.

The main [installation guide](https://github.com/mengwanguc/gpemu/blob/27e9534ee0c3d594030e2519d4a418b3f0a7860f/README.md)
uses custom PyTorch/TorchVision branches and a CPU-only PyTorch build. We did not
install those forks, change host limits, deploy Kubernetes or reboot this host.
Only pandas and its dependencies were installed in a separate temporary Python
environment for the small library check.

The included [Kubernetes device plugin](https://github.com/mengwanguc/gpemu/blob/27e9534ee0c3d594030e2519d4a418b3f0a7860f/gpemu-k8s/server.go)
creates synthetic device identities and maps configured host files into containers.
Its health loop assigns `Healthy`; it does not detect Xid79, lost PCIe devices
or real driver failure. It needs explicit unhealthy/quarantine/replacement
injection before it can exercise scheduler recovery scenarios.

## Experiment executed here

[check_gpemu.py](../experiments/check_gpemu.py) loads the unmodified, hash-checked
upstream timing module directly, avoiding package-wide broker imports. It uses
batch-size-1 ResNet18 CSV rows, checks all six profile families in sync, async and
cancel/restart modes, and exercises memory-cap refusal. Cancellation is injected
by our harness; it is not a simulated hardware Xid. No regression pickle loads.

All 18 timing cases passed. The separate interpolation probe reproduced the
missing import. [Results](../experiments/results/gpemu-assessment.json) include
upstream revision, module/profile hashes, pandas version and explicit limitations.
Passing these cases says nothing about real profile accuracy, numerical results,
firmware or electrical recovery. The recovery service itself was not exercised
by this workload-only check.

```bash
# Use a separate environment with pandas; no custom PyTorch build is needed.
python experiments/check_gpemu.py --repo /path/to/gpemu \
  --output /tmp/gpemu-assessment.json
```

## Where to use it next

Use GPEmu timing workloads to exercise drain latency, checkpoint cadence, queued
work, cancellation, restart and scheduler backpressure under injected incidents.
Its Kubernetes component could support a disposable scheduler experiment after
adding genuine modeled health transitions and quarantine semantics. Those service
integrations have not been implemented by this assessment.

Keep three evidence layers separate: GPEmu for workload behavior; QEMU plus
management fixtures for kernel PCI topology and orchestration; actual NVIDIA
hardware for firmware, reset wiring, coherent fabric and fresh CUDA recovery.
GPEmu must never replace the production CUDA probe or mark an enterprise GPU
recovery-qualified. It does not close the missing physical validation identified
in the [hardware survey](enterprise-hardware-landscape.md).

# Validate execution after recovery

An NVML response establishes that the management API can see a GPU. It does not
establish that a fresh application can create a CUDA context or execute work.
The generic controller now runs a built-in CUDA probe after its NVML identity
check unless a custom validation command replaces it.

The probe runs in a separate process with a 60-second limit enforced by the
controller. It uses Python's standard `ctypes` module and `libcuda.so.1`; no CUDA
toolkit, nvcc, NumPy, or Python CUDA bindings are required.

It performs these checks:

1. Select the physical GPU UUID through `CUDA_VISIBLE_DEVICES` and independently
   compare the CUDA-reported UUID.
2. Create a fresh CUDA context and allocate a small device buffer.
3. Copy 1,024 known integers from the host to the device.
4. JIT-load a small PTX kernel that adds seven to each element, launch it, and
   synchronize.
5. Copy the results back and compare every element before releasing resources.

Any API error, identity mismatch, result mismatch, or timeout fails recovery
verification. A successful reset command is not enough.

## Configuration

Default configuration:

```json
{"cuda_validation": true}
```

A root-managed `validation_command` takes precedence, including when
`cuda_validation` is false. Use a custom command for application-specific checks,
older architectures, or specialized deployments. It receives the selected UUID
through `CUDA_VISIBLE_DEVICES` and the PCI address through `GPU_BDF`.

With no custom command, setting `cuda_validation` to false explicitly skips the
built-in probe. The log then says CUDA execution was not verified. Existing
configurations that omit the setting now use the probe by default. Installations
must update both `gpu_recovery.py` and `cuda_probe.py`; the installer does this.

To run the probe independently, obtain the desired UUID from `nvidia-smi` and use
an external timeout:

```bash
timeout 60s python3 cuda_probe.py --uuid GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

This creates a small workload on the selected GPU. It does not reset the GPU.

## Compatibility and validation

The kernel uses PTX 6.4 targeting `sm_75`, intended for Turing and newer devices
with compatible CUDA driver/JIT support. That includes the architecture baseline
of the requested RTX 30/40/50 generations; only RTX 3090 execution has been
hardware-tested here. Older devices need a compatible custom validator. No claim
is made for future GPUs or restricted environments that do not permit this API.

On the physical RTX 3090, the standalone probe and the installed controller's
verification path passed. A nonexistent UUID failed initialization rather than
selecting another GPU. Unit tests also verify that a failed default probe blocks
success, UUID selection is passed through, and explicit opt-out reports reduced
validation. These runs tested a currently healthy GPU, not a new Xid79 recovery.

This is a small execution check, not a full memory, ECC, tensor-core, NVLink,
MIG, attestation, thermal, or stability diagnostic. It supplies no missing reset
or power-control mechanism and does not make recovery universal. JIT support,
permissions, compute mode and available resources can also make it fail.

References: [CUDA device identity API](https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__DEVICE.html),
[Driver API execution](https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__EXEC.html),
[PTX ISA](https://docs.nvidia.com/cuda/parallel-thread-execution/).

# Actual NVIDIA module against a dead PCI endpoint

This lab runs real `nvidia.ko` code inside an x86-64 Linux guest. A userspace
vfio-user server advertises NVIDIA PCI identity and two memory BARs, returning
all ones for every BAR read. It deliberately cannot initialize as a GPU.

No host device is passed through. QEMU and the endpoint run as the current user.
The NVIDIA module is inserted only inside the guest. No host module is installed
or loaded by this runner.

## Observed behavior

The experimental 615.71.09 core module, built for Linux 7.0.0-38, mapped the
synthetic registers, detected all-ones reads, printed that the GPU had fallen off
the bus, and rejected probe. Initialization unwound with no module left resident.
There was no timeout, fatal kernel error or guest reboot.

BAR-read counters and logs correspond to the all-ones branch in
[`rm_is_supported_device`](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/61dcc93722ecb418bb5f2e00923f05b4b8051dd1/src/nvidia/arch/nvalloc/unix/src/osapi.c).
This precedes architecture initialization. Changing PCI IDs therefore does not
exercise the different generations' firmware or reset routines.

| Advertised vendor/device | ID associated with |
| --- | --- |
| `10de:2204` | RTX 3090 |
| `10de:2684` | RTX 4090 |
| `10de:2b85` | RTX 5090 |
| `10de:20f1` | A100 PCIe 40 GB |
| `10de:2331` | H100 PCIe |

All five profiles produced the expected probe rejection. These associations
come from NVIDIA's [device list](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/ca2d03e6fafd4d4b56b383497d931c25b283bfcf/README.md).
They are identity fixtures, not card emulations. No firmware, VRAM, DMA engine
or compute behavior is modeled.

The candidate's modeset module is not loaded. Lost-event handling, callback
lifetime, active UVM teardown and removal of a previously initialized GPU remain
untested here. No comparison proving improvement over stock was performed.
The endpoint's reset callback acknowledges the protocol without restoring any
GPU functionality.

## Build

Requires a C compiler, Meson, Ninja, pkg-config, json-c and cmocka development
packages. Tested libvfio-user revision: `803924493a7c787b2ba358751f55f07d5dba64b2`.

```bash
git clone https://github.com/nutanix/libvfio-user.git /tmp/libvfio-user
git -C /tmp/libvfio-user checkout 803924493a7c787b2ba358751f55f07d5dba64b2
meson setup /tmp/libvfio-user/build /tmp/libvfio-user \
  -Dbuildtype=release -Ddebug-logs=disabled
ninja -C /tmp/libvfio-user/build lib/libvfio-user.so.0.0.1
cc -std=gnu11 -O2 -I /tmp/libvfio-user/include \
  experiments/qemu/dead_endpoint.c -L /tmp/libvfio-user/build/lib \
  -Wl,-rpath,/tmp/libvfio-user/build/lib -lvfio-user -o /tmp/dead-endpoint
```

Neither library nor endpoint needs system-wide installation. See
[libvfio-user](https://github.com/nutanix/libvfio-user) and
[QEMU vfio-user](https://www.qemu.org/docs/master/system/devices/vfio-user.html).

## Run

Requires the [base lab tools](README.md), QEMU with `vfio-user-pci`, `modinfo`,
a readable kernel, and a core module matching the guest kernel. An ABI rejection
fails the test. The recorded guest accepted the unsigned module and logged a
taint warning.

```bash
python3 experiments/qemu/run_nvidia_probe.py \
  --kernel /path/to/vmlinuz --module /path/to/nvidia.ko \
  --endpoint /tmp/dead-endpoint --device 2b85 \
  --output /tmp/nvidia-dead-probe.json
```

Guest and endpoint logs are retained. Module operations and VM execution are
bounded. `passed` means bounded rejection of the dead fixture, not GPU recovery.
`nvidia_recovery_verified` remains false. No CUDA, GSP restart, or restored
physical link is demonstrated.

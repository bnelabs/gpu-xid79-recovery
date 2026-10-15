# What the management firmware actually resets

NVIDIA's public OpenSMA firmware provides another side of the management path:
the receiver behind an NSM request. Reviewed revision:
`0c220c3a2d1de8cdc93804f1d86892814ef8a1ca`, matching upstream HEAD on
October 15, 2026.

## The command needs a platform implementation

The default `platform_assert_pcie_fundamental_reset` function returns
`NotSupported`. Its declaration describes a CPLD platform hook. No strong
implementation was found in this public checkout; a comment names a platform
override outside the files present here. The public default therefore supplies
no physical reset. See [the implementation](https://github.com/NVIDIA/OpenSMA/blob/0c220c3a2d1de8cdc93804f1d86892814ef8a1ca/src/nv/mctp/nsm_type_2.cpp).

This receiver also restricts the device index. Its named targets are NVSwitch,
a PCIe switch, and ConnectX-9 devices. The accepted values are `0x4f`, `0x50`,
`0x60` through `0x67`, and `0x70`. Index zero is rejected. See
[the target definitions](https://github.com/NVIDIA/OpenSMA/blob/0c220c3a2d1de8cdc93804f1d86892814ef8a1ca/src/nv/mctp/nsm_type_2.h).

This does not invalidate NSMD's separate GPU reset handler. It shows why the
endpoint's implementation and configured index must be identified: the same
command encoding is not a universal physical target map. An encoder accepting
an index says nothing about whether a particular receiver supports that target.

## A function name is not proof of GPU reset

OpenSMA's I3C `handle_gpu_reset` restores management-interface operation: it
enables interface mastering, configures interrupts, performs dynamic address
assignment, and checks entry into I3C mode. The reviewed function does not assert
a GPU ASIC reset. Its presence therefore does not supply a host-callable remedy
for a lost PCIe link. See [the I3C task](https://github.com/NVIDIA/OpenSMA/blob/0c220c3a2d1de8cdc93804f1d86892814ef8a1ca/src/nv/i3c/task.cpp).

The host x86 I3C backend contains TODO stubs, including methods that return true
without changing output parameters. Running that backend is not emulation of
real I3C hardware. A useful full-firmware emulator would need models for the
MCU, bus, endpoint, CPLD, and reset effects. See [the host backend](https://github.com/NVIDIA/OpenSMA/blob/0c220c3a2d1de8cdc93804f1d86892814ef8a1ca/src/sys/x86/sys/i3c/driver.cpp).

## Offline source replay

```bash
git clone https://github.com/NVIDIA/OpenSMA.git /tmp/opensma
git -C /tmp/opensma checkout 0c220c3a2d1de8cdc93804f1d86892814ef8a1ca
python3 experiments/replay_opensma_reset.py /tmp/opensma \
  --output /tmp/opensma-reset.json
```

The replay hash-checks its inputs and compiles the actual extracted target
validator, target definitions, status enum, and default hook with ASan/UBSan.
All 256 indices were checked: 11 admitted and 245 rejected. All 512 direct
default-hook calls, across those indices and actions 0/1, returned NotSupported.
Those calls exercise the hook directly, not the full packet dispatcher.

No MCU firmware image is booted, no board override runs, and no physical bus or
GPU is accessed. These results constrain the candidate implementation; they do
not establish NVIDIA hardware recovery. The full firmware was not built or
flashed. NVIDIA's [build instructions](https://github.com/NVIDIA/OpenSMA/blob/0c220c3a2d1de8cdc93804f1d86892814ef8a1ca/README.md)
target an MCU SDK and firmware toolchain, not an application installed on a GPU
server.

## Where this can help

| Hardware situation | Remaining requirement |
| --- | --- |
| Consumer card without an exposed management controller | A supported reset interface or independently mapped hardware control; this firmware cannot create one |
| Enterprise card with a management endpoint | Establish that endpoint's firmware, supported target index, transport and reset domain |
| Platform MCU controlling a CPLD | Supply and validate the platform-specific physical reset implementation |
| Host-only simulator using stub buses | Add independently validated peripheral/reset models before drawing hardware conclusions |

No OpenSMA reset backend is integrated into automatic recovery. The project still
requires a verified lifecycle adapter before using platform-specific management
or reset controls.

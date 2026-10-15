#!/usr/bin/env python3
"""Load an actual NVIDIA module against a dead synthetic PCI endpoint in a VM."""
import json
import os
import pathlib
import sys
import traceback


def main():
    if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():
        raise RuntimeError('Guest marker required')
    sys.path.insert(0,'/lab');import gpu_recovery as g
    root=pathlib.Path('/sys/bus/pci/devices')
    targets=[p for p in root.iterdir() if g.text(p/'vendor')=='0x10de']
    if len(targets)!=1:raise RuntimeError('Expected one synthetic NVIDIA-identity endpoint')
    target=targets[0];boot=g.text('/proc/sys/kernel/random/boot_id')
    baseline=g.run(['/bin/busybox','dmesg'])[1]
    timeout=None;load_rc=None;load_output=None;unload_rc=None;unload_output=None
    try:
        load_rc,load_output=g.run(['/bin/busybox','insmod','/lab/nvidia.ko'],15)
        if pathlib.Path('/sys/module/nvidia').exists():
            unload_rc,unload_output=g.run(['/bin/busybox','rmmod','nvidia'],15)
    except g.CommandTimeout as error:timeout=str(error)
    logs=g.run(['/bin/busybox','dmesg'])[1][len(baseline):]
    resident=pathlib.Path('/sys/module/nvidia').exists()
    rejected_abi=any(s in logs+(load_output or '') for s in ('Unknown symbol','invalid module format','version magic'))
    fatal=any(s in logs for s in ('Kernel panic','BUG:','Oops:'))
    driver_ran='NVRM:' in logs
    rejected_dead_endpoint=('NVIDIA GPU '+target.name in logs and 'fallen off the bus' in logs
                            and 'probe with driver nvidia failed' in logs)
    same_boot=g.text('/proc/sys/kernel/random/boot_id')==boot
    print('GPU_QEMU_RESULT='+json.dumps({
        'kind':'Actual NVIDIA module, deliberately dead synthetic PCI endpoint; not GPU recovery',
        'kernel':os.uname().release,'target':target.name,'vendor':g.text(target/'vendor'),
        'device':g.text(target/'device'),'load_returncode':load_rc,'load_output':load_output,
        'unload_returncode':unload_rc,'unload_output':unload_output,'timeout':timeout,
        'module_resident_after_test':resident,'driver_log_observed':driver_ran,
        'module_abi_rejected':rejected_abi,'dead_endpoint_probe_rejected':rejected_dead_endpoint,
        'kernel_fatal_observed':fatal,'boot_unchanged':same_boot,
        'kernel_log':logs,'passed':driver_ran and rejected_dead_endpoint and not rejected_abi and not fatal and not resident and timeout is None and same_boot,
        'nvidia_recovery_verified':False,
        'limitations':'No valid GPU boot registers, firmware, VRAM, DMA engines or CUDA. Probe rejection does not exercise removal of a previously initialized GPU.'
    }),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

#!/usr/bin/env python3
"""Observe real guest AER handling; synthetic PCI endpoints, no NVIDIA driver."""
import json
import os
import pathlib
import subprocess
import time
import traceback


def main():
    if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():
        raise RuntimeError('Guest marker required')
    root=pathlib.Path('/sys/bus/pci/devices')
    devices=sorted(p for p in root.iterdir() if (p/'vendor').read_text().strip()=='0x1b36'
                   and (p/'device').read_text().strip()=='0x0005')
    if len(devices)!=2:raise RuntimeError('Expected two test endpoints')
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text()
    before={p.name:(p/'config').read_bytes()[:4].hex() for p in devices}
    baseline=subprocess.check_output(['/bin/busybox','dmesg'],text=True)
    driver_expected=pathlib.Path('/lab/aer_test_driver.ko').exists()
    drivers={p.name:(p/'driver').resolve().name if (p/'driver').exists() else None for p in devices}
    if driver_expected and any(driver!='aer_test_driver' for driver in drivers.values()):
        raise RuntimeError('Guest test driver failed to bind: '+repr(drivers))
    print('GPU_AER_READY',flush=True)
    deadline=time.monotonic()+30
    logs=''
    while time.monotonic()<deadline:
        logs=subprocess.check_output(['/bin/busybox','dmesg'],text=True)[len(baseline):]
        if 'device recovery successful' in logs or 'device recovery failed' in logs:break
        time.sleep(.1)
    after={p.name:(p/'config').read_bytes()[:4].hex() if p.exists() else None for p in devices}
    same_boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text()==boot
    observed='AER:' in logs and ('Fatal' in logs or 'fatal' in logs)
    completed='device recovery successful' in logs
    callbacks=all('LAB_AER '+event in logs for event in ('error_detected','slot_reset','resume'))
    peer_callback_observed=any(devices[1].name in line and 'LAB_AER' in line for line in logs.splitlines())
    expected_outcome=(completed and callbacks) if driver_expected else ('no error_detected callback' in logs and 'device recovery failed' in logs)
    print('GPU_QEMU_RESULT='+json.dumps({
        'kind':'Real guest AER handling of synthetic error; no NVIDIA hardware or driver',
        'architecture':os.uname().machine,'kernel':os.uname().release,
        'before_identity':before,'after_identity':after,'boot_unchanged':same_boot,
        'drivers':drivers,'callback_sequence_observed':callbacks,'peer_callback_observed':peer_callback_observed,
        'fatal_aer_observed':observed,'kernel_recovery_success_message':completed,
        'kernel_log':logs,'passed':observed and same_boot and before==after and expected_outcome and not peer_callback_observed,
        'nvidia_recovery_verified':False,
        'limitations':'Root-port AER status injection does not reproduce electrical link loss, NVIDIA driver callbacks, GSP failure or CUDA execution.'
    }),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

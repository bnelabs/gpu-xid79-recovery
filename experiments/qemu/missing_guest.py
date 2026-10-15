#!/usr/bin/env python3
"""Logical removal/rediscovery experiment, confined to synthetic guest devices."""
import json
import os
import pathlib
import sys
import time
import traceback


def main():
    if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():
        raise RuntimeError('Guest marker required')
    sys.path.insert(0,'/lab');import gpu_recovery as g
    root=pathlib.Path('/sys/bus/pci/devices')
    devices=sorted(p for p in root.iterdir() if g.text(p/'vendor')=='0x1b36' and g.text(p/'device')=='0x0005')
    if len(devices)!=2:raise RuntimeError('Expected two synthetic endpoints')
    target,peer=devices
    g.discover=lambda root=root:[p.name for p in devices if p.exists()]
    import topology_snapshot as cached
    selected=g.inspect(target.name)
    cached_snapshot=cached.capture(target.name)
    if not selected.isolated:raise RuntimeError('Target is not isolated')
    identity=lambda p:(p/'config').read_bytes()[:4].hex() if p.exists() else None
    boot=g.text('/proc/sys/kernel/random/boot_id')
    snapshot={'boot_id':boot,'target':selected.bdf,'target_identity':identity(target),
              'bridge':selected.bridge,'bridge_identity':identity(root/selected.bridge),
              'bridge_path':str((root/selected.bridge).resolve()),'peer_identity':identity(peer)}
    g.write_sysfs(target/'remove','1')
    removed=not target.exists()
    error=None
    try:g.inspect(selected.bdf)
    except g.RecoveryError as exc:error=str(exc)
    # Cached data never bypasses inspect in production. This guest experiment
    # explicitly checks the surviving parent before a parent-scoped rescan.
    bridge=root/snapshot['bridge']
    parent_unchanged=(identity(bridge)==snapshot['bridge_identity']
                      and str(bridge.resolve())==snapshot['bridge_path']
                      and g.text('/proc/sys/kernel/random/boot_id')==boot)
    remaining=sorted(p.name for p in root.iterdir() if bridge.resolve() in p.resolve().parents)
    if not parent_unchanged or remaining:raise RuntimeError('Parent identity or empty scope changed')
    cached_assessment=cached.assess(cached_snapshot)
    if not cached_assessment['snapshot_consistent'] or cached_assessment['target_present']:
        raise RuntimeError('Cached topology check failed: '+str(cached_assessment))
    rescan=g.subordinate_rescan_path(snapshot['bridge'])
    if rescan is None:raise RuntimeError('Unique subordinate bus rescan not exposed')
    g.write_sysfs(rescan,'1')
    deadline=time.monotonic()+5
    while not target.exists() and time.monotonic()<deadline:time.sleep(.1)
    returned=identity(target)==snapshot['target_identity']
    peer_unchanged=identity(peer)==snapshot['peer_identity']
    same_boot=g.text('/proc/sys/kernel/random/boot_id')==boot
    print('GPU_QEMU_RESULT='+json.dumps({
        'kind':'Logical PCI removal and parent-scoped rediscovery; not electrical GPU recovery',
        'architecture':os.uname().machine,'kernel':os.uname().release,
        'cached_topology_assessment':cached_assessment,
        'snapshot':snapshot,'target_removed':removed,'normal_inspect_error':error,
        'parent_unchanged':parent_unchanged,'rescan_path':str(rescan),'target_returned_same_vendor_device':returned,
        'peer_identity_unchanged':peer_unchanged,'boot_unchanged':same_boot,
        'passed':removed and error is not None and returned and peer_unchanged and same_boot,
        'nvidia_recovery_verified':False,
        'limitations':'sysfs removal leaves the emulated endpoint electrically present. Matching vendor/device and BDF is not unique device identity. No NVIDIA driver, firmware restart, or CUDA execution tested.'
    }),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

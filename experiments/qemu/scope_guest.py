#!/usr/bin/env python3
"""Exercise the actual controller with storage and multi-GPU scope fixtures."""
import json
import mmap
import os
import pathlib
import sys
import traceback


def main():
    words=pathlib.Path('/proc/cmdline').read_text().split()
    if 'gpu_recovery_lab=1' not in words:raise RuntimeError('Guest marker required')
    role=next(word.split('=',1)[1] for word in words if word.startswith('lab_peer='))
    shared='lab_shared=1' in words
    sys.path.insert(0,'/lab');import gpu_recovery as g
    root=pathlib.Path('/sys/bus/pci/devices')
    endpoints=sorted(p for p in root.iterdir() if g.text(p/'vendor')=='0x1b36' and g.text(p/'device')=='0x0005')
    target=endpoints[0]
    if role=='nvme':
        peers=[p for p in root.iterdir() if g.text(p/'class')=='0x010802']
        if len(peers)!=1:raise RuntimeError('Expected exactly one NVMe controller')
        peer=peers[0];admitted=[target.name]
    else:
        if len(endpoints)!=2:raise RuntimeError('Expected two synthetic endpoints')
        peer=endpoints[1];admitted=[p.name for p in endpoints]
    # Synthetic identity admission is explicit; NVIDIA discovery is not tested.
    g.discover=lambda root=root:admitted
    selected=g.inspect(target.name)
    identity=lambda p:(p/'config').read_bytes()[:4].hex() if p.exists() else None
    before=identity(peer);boot=g.text('/proc/sys/kernel/random/boot_id')
    with (target/'config').open('r+b',buffering=0) as stream:
        stream.seek(4);command=int.from_bytes(stream.read(2),'little');stream.seek(4);stream.write((command|2).to_bytes(2,'little'))
    fd=os.open(target/'resource0',os.O_RDWR)
    try:region=mmap.mmap(fd,4096)
    finally:os.close(fd)
    region[0]=0;region[2048]=0xfa
    initial_count=int.from_bytes(region[12:16],'little')
    if initial_count!=1:raise RuntimeError('Target counter failed to initialize')
    writes=[];native=g.write_sysfs
    def write(path,value):
        writes.append({'path':str(path),'value':value});return native(path,value)
    g.write_sysfs=write
    verified=[]
    g.verify=lambda target,cfg:verified.append(int.from_bytes(region[12:16],'little'))
    error=None
    try:g.recover(selected,{'kernel_reset_mode':'bus','services':[]})
    except g.RecoveryError as exc:error=str(exc)
    after=identity(peer);count=int.from_bytes(region[12:16],'little')
    same_boot=g.text('/proc/sys/kernel/random/boot_id')==boot
    refusal=role=='gpu' or shared
    if refusal:
        reason='Multi-GPU' if role=='gpu' else 'bridge contains other devices'
        outcome=error is not None and reason in error and not writes and count==1 and not verified
    else:outcome=error is None and count==0 and verified==[0] and len(writes)==1
    print('GPU_QEMU_RESULT='+json.dumps({
        'kind':'Real controller/kernel scope experiment; synthetic GPU identity and verification',
        'architecture':os.uname().machine,'kernel':os.uname().release,'peer_role':role,
        'shared_bridge':shared,'target':target.name,'peer':peer.name,'isolated':selected.isolated,
        'peer_identity_before':before,'peer_identity_after':after,'sysfs_writes':writes,
        'target_counter_after':count,'error':error,'boot_unchanged':same_boot,
        'passed':outcome and before is not None and before==after and same_boot,
        'nvidia_recovery_verified':False,
        'limitations':'NVMe has no namespace or storage workload; peer identity alone does not establish storage integrity. GPU identities and final verification are substituted; no NVIDIA firmware or fabric is emulated.'
    }),flush=True)
    region.close()


if __name__=='__main__':
    try:main()
    except Exception:print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

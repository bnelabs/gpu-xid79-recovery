#!/usr/bin/env python3
"""Guest-only synthetic adapter. No host management transport or GPU commands."""
import json,os,pathlib,sys
if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():raise RuntimeError('Lab guest marker required')
sys.path.insert(0,'/lab');import gpu_recovery as g
members=json.loads(os.environ['GPU_DOMAIN_MEMBERS']);scope=json.loads(os.environ['GPU_DOMAIN_SCOPE'])
if sorted(members)!=scope or len(members)!=2:raise RuntimeError('Expected exact synthetic two-device scope')
parents=set()
for bdf in members:
 if not g.BDF.fullmatch(bdf):raise RuntimeError('Malformed lab member')
 device=g.SYSFS/bdf
 if g.text(device/'vendor')!='0x1b36' or g.text(device/'device')!='0x0005':raise RuntimeError('Not a lab testdev')
 parents.add(device.resolve().parent)
actual={p.name for parent in parents for p in parent.rglob('*') if g.BDF.fullmatch(p.name)}
if actual!=set(scope):raise RuntimeError('Synthetic adapter scope mismatch')
phase=os.environ['GPU_RECOVERY_PHASE']
if phase=='recover':
 for parent in sorted(parents):g.write_sysfs(parent/'reset_subordinate','1')
value={'schema':1,'nonce':os.environ['GPU_RECOVERY_NONCE'],'boot_id':g.text('/proc/sys/kernel/random/boot_id'),
 'members':members,'scope_bdfs':scope,'exclusive_local_control':True,'host_reboot_required':False,
 'clients_quiesced':True,'ready':True,'workloads_verified':True,'fabric_verified':True}
print(json.dumps(value))

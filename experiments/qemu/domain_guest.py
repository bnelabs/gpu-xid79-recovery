#!/usr/bin/env python3
"""Real guest PCI domain reset with synthetic identities and counter validation."""
import json,mmap,os,pathlib,sys,traceback


def main():
 if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():raise RuntimeError('Lab guest marker required')
 sys.path.insert(0,'/lab');import gpu_recovery as g
 root=g.SYSFS
 devices=sorted(p for p in root.iterdir() if g.text(p/'vendor')=='0x1b36' and g.text(p/'device')=='0x0005')
 if len(devices)!=2:raise RuntimeError('Expected two synthetic endpoints')
 bdfs=[p.name for p in devices];g.discover=lambda root=root:list(bdfs)
 native_text=g.text
 def fixture_text(path,*args):
  path=pathlib.Path(path)
  if path==g.DMI/'product_name':return 'Synthetic domain fixture'
  if path.parent.name in bdfs and path.name in ('vendor','class'):
   return '0x10de' if path.name=='vendor' else '0x030000'
  return native_text(path,*args)
 g.text=fixture_text
 mappings={}
 for device in devices:
  with (device/'config').open('r+b',buffering=0) as stream:
   stream.seek(4);value=int.from_bytes(stream.read(2),'little');stream.seek(4);stream.write((value|2).to_bytes(2,'little'))
  fd=os.open(device/'resource0',os.O_RDWR)
  try:region=mmap.mmap(fd,4096)
  finally:os.close(fd)
  region[0]=0;region[2048]=0xfa;mappings[device.name]=region
 def counts():return {bdf:int.from_bytes(region[12:16],'little') for bdf,region in mappings.items()}
 before=counts();boot=g.text('/proc/sys/kernel/random/boot_id')
 approved=dict(zip(bdfs,['GPU-11111111-2222-3333-4444-555555555555','GPU-66666666-7777-8888-9999-aaaaaaaaaaaa']))
 verified=[]
 def verify(target,cfg):
  if counts()[target.bdf]!=0 or cfg['expected_gpu_uuids']!=approved:raise g.RecoveryError('Domain counter validation failed')
  verified.append(target.bdf)
 g.verify=verify
 command=['/usr/bin/python3','/lab/domain_backend.py']
 backend={'command':command,'prefer_backend':True,'match':{'product_name':g.text(g.DMI/'product_name')},
          'domain':{'members':approved,'preflight_command':command,'validation_command':command}}
 result=g.recover(g.inspect(bdfs[0]),{'backends':{bdf:backend for bdf in bdfs}})
 after=counts();same_boot=g.text('/proc/sys/kernel/random/boot_id')==boot
 passed=result==bdfs and verified==bdfs and all(v==1 for v in before.values()) and all(v==0 for v in after.values()) and same_boot
 print('GPU_QEMU_RESULT='+json.dumps({'kind':'Real guest kernel multi-device reset through domain adapter; synthetic NVIDIA identities',
  'architecture':os.uname().machine,'kernel':os.uname().release,'members':bdfs,'before':before,'after':after,
  'verified_members':verified,'boot_unchanged':same_boot,'passed':passed,'nvidia_recovery_verified':False,
  'substitutions':['QEMU testdev admission and NVIDIA vendor/class plus platform identity substitution','No configured services; no tenant or fabric lifecycle','Counter checks replace NVML/CUDA verification','Lab backend receipts describe synthetic scope, not real GPU controller evidence']}),flush=True)
 for region in mappings.values():region.close()


if __name__=='__main__':
 try:main()
 except Exception:print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

#!/usr/bin/env python3
"""Guest-only controller integration test using QEMU's PCI test devices."""
import json,mmap,os,pathlib,sys,traceback

ROOT=pathlib.Path('/sys/bus/pci/devices')


def main():
 if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():
  raise RuntimeError('Refusing to run outside the explicitly marked lab guest')
 sys.path.insert(0,'/lab');import gpu_recovery as g
 devices=sorted(p for p in ROOT.iterdir() if g.text(p/'vendor')=='0x1b36' and g.text(p/'device')=='0x0005')
 if len(devices)!=2:raise RuntimeError('Expected exactly two QEMU PCI test devices')
 target,peer=devices
 shared='lab_shared=1' in pathlib.Path('/proc/cmdline').read_text().split()
 # Identity injection admits only this synthetic endpoint to the controller.
 # NVIDIA identity validation and NVML are NOT tested by this fixture.
 g.discover=lambda root=ROOT:[target.name]
 inspected=g.inspect(target.name)
 boot=g.text('/proc/sys/kernel/random/boot_id')
 mappings=[]
 for device in devices:
  with (device/'config').open('r+b',buffering=0) as stream:
   stream.seek(4);command=int.from_bytes(stream.read(2),'little')
   stream.seek(4);stream.write((command|2).to_bytes(2,'little'))
  fd=os.open(device/'resource0',os.O_RDWR)
  try:region=mmap.mmap(fd,4096)
  finally:os.close(fd)
  # Activate test zero and increment its documented byte-access counter.
  region[0]=0;region[2048]=0xfa
  mappings.append(region)
 def count(region):return int.from_bytes(region[12:16],'little')
 before=[count(region) for region in mappings]
 if before!=[1,1]:raise RuntimeError('Virtual endpoint counters did not initialize')
 native_write=g.write_sysfs;writes=[]
 def record_write(path,value):
  writes.append({'path':str(path),'value':value});return native_write(path,value)
 g.write_sysfs=record_write
 verified=[]
 def verify_model(unused_target,unused_cfg):
  verified.append({'counters':[count(region) for region in mappings],
                   'boot_unchanged':g.text('/proc/sys/kernel/random/boot_id')==boot})
 g.verify=verify_model
 error=None
 try:g.recover(inspected,{'kernel_reset_mode':'bus','services':[]})
 except g.RecoveryError as exc:error=str(exc)
 after=[count(region) for region in mappings]
 if shared:
  passed=error is not None and ('isolation' in error.lower() or 'bridge contains other devices' in error)
  passed=passed and not writes and after==before and not verified
 else:
  passed=error is None and after==[0,1] and len(verified)==1 and verified[0]['boot_unchanged']
 result={'kind':'Real guest Linux/controller, synthetic PCI endpoints; not NVIDIA GPU recovery',
         'architecture':os.uname().machine,'kernel':os.uname().release,'shared_bridge':shared,
         'target':target.name,'peer':peer.name,'bridge':inspected.bridge,'isolated':inspected.isolated,
         'before':before,'after':after,'sysfs_writes':writes,'error':error,'verification':verified,
         'boot_unchanged':g.text('/proc/sys/kernel/random/boot_id')==boot,'passed':bool(passed),
         'substitutions':['Only the selected QEMU 1b36:0005 endpoint is admitted by discover',
                         'nvidia-smi is a guest stub returning unsupported reset and no compute clients',
                         'Final verification checks emulated endpoint counters and guest boot ID, not NVML/CUDA']}
 print('GPU_QEMU_RESULT='+json.dumps(result),flush=True)
 for region in mappings:region.close()

if __name__=='__main__':
 try:main()
 except Exception:
  traceback.print_exc();print('GPU_QEMU_RESULT='+json.dumps({'passed':False,'error':traceback.format_exc()}),flush=True)

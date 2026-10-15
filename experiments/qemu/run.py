#!/usr/bin/env python3
"""Boot a disposable Linux/QEMU reset lab. Never passes through host devices."""
import argparse,gzip,json,os,pathlib,re,shutil,socket,subprocess,tempfile,time


def copy_file(source,root,destination=None):
 source=pathlib.Path(source);destination=root/(destination or str(source).lstrip('/'))
 destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source.resolve(),destination)


def build_root(root,userspace,slot_power=False,aer=False,aer_driver=None,scope_peer=None,missing=False):
 if userspace:
  shutil.copytree(userspace,root,dirs_exist_ok=True,symlinks=True)
 else:
  copy_file('/usr/bin/busybox',root,'bin/busybox')
  executable=pathlib.Path('/usr/bin/python3').resolve();copy_file(executable,root)
  lib=pathlib.Path('/usr/lib')/('python'+'.'.join(subprocess.check_output([str(executable),'-c','import sys;print(".".join(map(str,sys.version_info[:2])))'],text=True).strip().split('.')))
  shutil.copytree(lib,root/str(lib).lstrip('/'),ignore=shutil.ignore_patterns('__pycache__'),symlinks=False)
  objects=[executable,*lib.rglob('*.so')];libraries=set()
  for item in objects:
   output=subprocess.run(['ldd',str(item)],capture_output=True,text=True).stdout
   for name in re.findall(r'(?:=>\s*)?(/[^\s()]+)',output):libraries.add(name)
  for name in libraries:copy_file(name,root)
  (root/'usr/bin/python3').symlink_to(executable.name)
 for name in ('proc','sys','dev','run','tmp','lab','bin','usr/bin'):(root/name).mkdir(parents=True,exist_ok=True)
 busybox=root/'bin/busybox'
 if not busybox.exists():raise RuntimeError('Userspace requires static /bin/busybox')
 (root/'bin/sh').unlink(missing_ok=True);(root/'bin/sh').symlink_to('busybox')
 # The real production controller is copied unchanged into the guest.
 repo=pathlib.Path(__file__).resolve().parents[2]
 copy_file(repo/'gpu_recovery.py',root,'lab/gpu_recovery.py')
 copy_file(pathlib.Path(__file__).with_name('missing_guest.py' if missing else 'scope_guest.py' if scope_peer else 'aer_guest.py' if aer else 'slot_power_guest.py' if slot_power else 'guest.py'),root,'lab/guest.py')
 if aer_driver:copy_file(aer_driver,root,'lab/aer_test_driver.ko')
 stub=root/'usr/bin/nvidia-smi'
 stub.write_text('#!/bin/sh\ncase "$*" in *--query-compute-apps=pid*) exit 0;; *) exit 1;; esac\n');stub.chmod(0o755)
 init=root/'init';init.write_text('''#!/bin/sh
export PATH=/usr/bin:/bin
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
[ ! -f /lab/aer_test_driver.ko ] || /bin/busybox insmod /lab/aer_test_driver.ko
/usr/bin/python3 /lab/guest.py
/bin/busybox poweroff -f
''');init.chmod(0o755)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--kernel',required=True,type=pathlib.Path)
 parser.add_argument('--arch',choices=['x86_64','aarch64'],default='x86_64')
 parser.add_argument('--userspace',type=pathlib.Path,help='Extracted matching guest userspace; required for ARM64')
 parser.add_argument('--shared',action='store_true');parser.add_argument('--output',required=True,type=pathlib.Path)
 parser.add_argument('--slot-power',action='store_true',help='Experiment with isolated guest slot power; no NVIDIA recovery claim')
 parser.add_argument('--aer',action='store_true',help='Inject a synthetic fatal AER into the guest root port')
 parser.add_argument('--aer-driver',type=pathlib.Path,help='Synthetic testdev kernel module loaded inside the AER guest only')
 parser.add_argument('--scope-peer',choices=['nvme','gpu'],help='Test recovery scope with an NVMe controller or a second admitted synthetic GPU')
 parser.add_argument('--missing',action='store_true',help='Test guest logical removal and scoped rediscovery; no physical link-loss simulation')
 args=parser.parse_args()
 if args.slot_power and args.shared:parser.error('Slot-power experiment requires separate bridges')
 if args.aer and (args.shared or args.slot_power):parser.error('AER experiment requires separate bridges and no slot-power mode')
 if args.aer_driver and not args.aer:parser.error('--aer-driver requires --aer')
 if args.scope_peer and (args.aer or args.slot_power):parser.error('--scope-peer cannot be combined with AER or slot-power')
 if args.missing and (args.scope_peer or args.aer or args.slot_power or args.shared):parser.error('--missing requires separate bridges and no other experiment mode')
 if args.arch=='aarch64' and not args.userspace:parser.error('ARM64 requires matching guest userspace')
 if not args.userspace and os.uname().machine!='x86_64':parser.error('Automatic userspace construction currently supports x86_64 hosts')
 with tempfile.TemporaryDirectory(prefix='gpu-qemu-') as directory:
  base=pathlib.Path(directory);root=base/'root';root.mkdir();build_root(root,args.userspace,args.slot_power,args.aer,args.aer_driver,args.scope_peer,args.missing)
  names=[str(path.relative_to(root)) for path in sorted(root.rglob('*'))]
  packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],cwd=root,
                        input=('\0'.join(names)+'\0').encode(),capture_output=True,check=True)
  initrd=base/'initrd.gz';initrd.write_bytes(gzip.compress(packed.stdout,compresslevel=1))
  machine='q35' if args.arch=='x86_64' else 'virt'
  cpu='max' if args.arch=='x86_64' else 'cortex-a57'
  console='ttyS0' if args.arch=='x86_64' else 'ttyAMA0'
  command=[f'qemu-system-{args.arch}','-machine',machine,'-accel','tcg','-cpu',cpu,'-smp','2','-m','768',
           '-nodefaults','-no-user-config','-display','none','-serial','stdio','-monitor','none','-nic','none',
           '-no-reboot','-kernel',str(args.kernel.resolve()),'-initrd',str(initrd),
           '-append',f'console={console} panic=-1 quiet gpu_recovery_lab=1'+(' lab_shared=1' if args.shared else '')+(' lab_peer='+args.scope_peer if args.scope_peer else ''),
           '-device','pcie-root-port,id=rp1,bus=pcie.0,addr=2,chassis=1',
           ]
  if args.shared:
   command+=['-device','pci-bridge,id=shared,bus=rp1,addr=0,chassis_nr=3']
  command+=['-device','pci-testdev,id=target,bus='+('shared,addr=1' if args.shared else 'rp1,addr=0')]
  if not args.shared:command+=['-device','pcie-root-port,id=rp2,bus=pcie.0,addr=3,chassis=2']
  peer_bus='shared,addr=2' if args.shared else 'rp2,addr=0'
  if args.scope_peer=='nvme':command+=['-device','nvme,id=peer,serial=LAB-NVME,bus='+peer_bus]
  else:command+=['-device','pci-testdev,id=peer,bus='+peer_bus]
  args.output.parent.mkdir(parents=True,exist_ok=True)
  console_log=args.output.with_suffix('.console.log')
  injection=None
  if args.aer:command+=['-qmp',f'unix:{base}/qmp.sock,server=on,wait=off']
  with console_log.open('w') as log:
   with subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,text=True) as process:
    try:
     if args.aer:
      deadline=time.monotonic()+60
      while 'GPU_AER_READY' not in console_log.read_text():
       if process.poll() is not None:raise RuntimeError('Guest exited before AER readiness')
       if time.monotonic()>deadline:raise RuntimeError('AER readiness timed out')
       time.sleep(.1)
      with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
       connection.settimeout(10);connection.connect(str(base/'qmp.sock'))
       stream=connection.makefile('rwb');json.loads(stream.readline())
       def qmp(execute,arguments=None):
        message={'execute':execute}
        if arguments is not None:message['arguments']=arguments
        stream.write((json.dumps(message)+'\n').encode());stream.flush()
        while True:
         response=json.loads(stream.readline())
         if 'error' in response:raise RuntimeError(str(response['error']))
         if 'return' in response:return response['return']
       qmp('qmp_capabilities')
       injection=qmp('human-monitor-command',{'command-line':'pcie_aer_inject_error rp1 0x20'})
       if injection.strip() and not injection.startswith('OK id: rp1 '):raise RuntimeError('AER injection failed: '+injection)
     returncode=process.wait(timeout=180)
    finally:
     if process.poll() is None:process.kill();process.wait(timeout=10)
  console_text=console_log.read_text()
  lines=[line for line in console_text.splitlines() if line.startswith('GPU_QEMU_RESULT=')]
  if not lines:raise RuntimeError('Guest produced no result; inspect '+str(args.output.with_suffix('.console.log')))
  report=json.loads(lines[-1].split('=',1)[1]);report['qemu_returncode']=returncode
  if args.aer:report['injection']={'command':'pcie_aer_inject_error rp1 0x20','monitor_response':injection}
  report['qemu_version']=subprocess.check_output([command[0],'-version'],text=True).splitlines()[0]
  args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
  if returncode or not report.get('passed'):raise SystemExit(1)

if __name__=='__main__':main()

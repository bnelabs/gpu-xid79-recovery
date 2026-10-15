#!/usr/bin/env python3
"""Boot a disposable Linux/QEMU reset lab. Never passes through host devices."""
import argparse,gzip,json,os,pathlib,re,shutil,subprocess,tempfile


def copy_file(source,root,destination=None):
 source=pathlib.Path(source);destination=root/(destination or str(source).lstrip('/'))
 destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source.resolve(),destination)


def build_root(root,userspace):
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
 copy_file(pathlib.Path(__file__).with_name('guest.py'),root,'lab/guest.py')
 stub=root/'usr/bin/nvidia-smi'
 stub.write_text('#!/bin/sh\ncase "$*" in *--query-compute-apps=pid*) exit 0;; *) exit 1;; esac\n');stub.chmod(0o755)
 init=root/'init';init.write_text('''#!/bin/sh
export PATH=/usr/bin:/bin
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
/usr/bin/python3 /lab/guest.py
/bin/busybox poweroff -f
''');init.chmod(0o755)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--kernel',required=True,type=pathlib.Path)
 parser.add_argument('--arch',choices=['x86_64','aarch64'],default='x86_64')
 parser.add_argument('--userspace',type=pathlib.Path,help='Extracted matching guest userspace; required for ARM64')
 parser.add_argument('--shared',action='store_true');parser.add_argument('--output',required=True,type=pathlib.Path)
 args=parser.parse_args()
 if args.arch=='aarch64' and not args.userspace:parser.error('ARM64 requires matching guest userspace')
 if not args.userspace and os.uname().machine!='x86_64':parser.error('Automatic userspace construction currently supports x86_64 hosts')
 with tempfile.TemporaryDirectory(prefix='gpu-qemu-') as directory:
  base=pathlib.Path(directory);root=base/'root';root.mkdir();build_root(root,args.userspace)
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
           '-append',f'console={console} panic=-1 quiet gpu_recovery_lab=1'+(' lab_shared=1' if args.shared else ''),
           '-device','pcie-root-port,id=rp1,bus=pcie.0,addr=2,chassis=1',
           ]
  if args.shared:
   command+=['-device','pci-bridge,id=shared,bus=rp1,addr=0,chassis_nr=3']
  command+=['-device','pci-testdev,id=target,bus='+('shared,addr=1' if args.shared else 'rp1,addr=0')]
  if args.shared:command+=['-device','pci-testdev,id=peer,bus=shared,addr=2']
  else:command+=['-device','pcie-root-port,id=rp2,bus=pcie.0,addr=3,chassis=2','-device','pci-testdev,id=peer,bus=rp2,addr=0']
  args.output.parent.mkdir(parents=True,exist_ok=True)
  console_log=args.output.with_suffix('.console.log')
  with console_log.open('w') as log:
   result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,text=True,timeout=180)
  console_text=console_log.read_text()
  lines=[line for line in console_text.splitlines() if line.startswith('GPU_QEMU_RESULT=')]
  if not lines:raise RuntimeError('Guest produced no result; inspect '+str(args.output.with_suffix('.console.log')))
  report=json.loads(lines[-1].split('=',1)[1]);report['qemu_returncode']=result.returncode
  report['qemu_version']=subprocess.check_output([command[0],'-version'],text=True).splitlines()[0]
  args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
  if result.returncode or not report.get('passed'):raise SystemExit(1)

if __name__=='__main__':main()

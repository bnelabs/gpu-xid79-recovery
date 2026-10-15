#!/usr/bin/env python3
"""Isolate actual NVIDIA module probing in QEMU with a userspace dead endpoint."""
import argparse
import gzip
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time
from run import build_root, copy_file


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel',required=True,type=pathlib.Path)
    parser.add_argument('--module',required=True,type=pathlib.Path)
    parser.add_argument('--endpoint',required=True,type=pathlib.Path)
    parser.add_argument('--device',choices=['2204','2684','2b85','20f1','2331'],required=True)
    parser.add_argument('--output',required=True,type=pathlib.Path)
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    if os.uname().machine!='x86_64':parser.error('This runner currently builds x86-64 guests only')
    with tempfile.TemporaryDirectory(prefix='nvidia-probe-vm-') as directory:
        base=pathlib.Path(directory);root=base/'root';root.mkdir();build_root(root,None)
        copy_file(pathlib.Path(__file__).with_name('nvidia_probe_guest.py'),root,'lab/guest.py')
        copy_file(args.module,root,'lab/nvidia.ko')
        names=[str(p.relative_to(root)) for p in sorted(root.rglob('*'))]
        archive=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],cwd=root,
                               input=('\0'.join(names)+'\0').encode(),capture_output=True,check=True)
        initrd=base/'initrd.gz';initrd.write_bytes(gzip.compress(archive.stdout,compresslevel=1))
        sock=base/'vfio.sock'
        console=args.output.with_suffix('.console.log');endpoint_log=args.output.with_suffix('.endpoint.log')
        with endpoint_log.open('w') as elog,console.open('w') as log:
            with subprocess.Popen([str(args.endpoint.resolve()),str(sock),args.device],stdout=elog,stderr=elog) as server:
                try:
                    deadline=time.monotonic()+10
                    while not sock.exists():
                        if server.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Endpoint failed to start')
                        time.sleep(.05)
                    command=['qemu-system-x86_64','-machine','q35','-accel','tcg','-cpu','max','-smp','2','-m','1024',
                             '-nodefaults','-no-user-config','-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot',
                             '-kernel',str(args.kernel.resolve()),'-initrd',str(initrd),
                             '-append','console=ttyS0 quiet panic=-1 gpu_recovery_lab=1',
                             '-device','pcie-root-port,id=rp1,bus=pcie.0,addr=2,chassis=1',
                             '-device',json.dumps({'driver':'vfio-user-pci','socket':{'type':'unix','path':str(sock)},'bus':'rp1','addr':'0'})]
                    result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=90)
                    try:server.wait(timeout=3)
                    except subprocess.TimeoutExpired:server.terminate()
                finally:
                    if server.poll() is None:server.kill()
                    server.wait(timeout=5)
        lines=[s for s in console.read_text().splitlines() if s.startswith('GPU_QEMU_RESULT=')]
        if not lines:raise RuntimeError('No guest result; inspect '+str(console))
        report=json.loads(lines[-1].split('=',1)[1]);report['qemu_returncode']=result.returncode
        report['module_sha256']=hashlib.sha256(args.module.read_bytes()).hexdigest()
        report['module_version']=subprocess.check_output(['modinfo','-F','version',str(args.module)],text=True).strip()
        report['module_vermagic']=subprocess.check_output(['modinfo','-F','vermagic',str(args.module)],text=True).strip()
        report['endpoint_sha256']=hashlib.sha256(args.endpoint.read_bytes()).hexdigest()
        report['qemu_version']=subprocess.check_output(['qemu-system-x86_64','--version'],text=True).splitlines()[0]
        report['endpoint_log']=endpoint_log.read_text()
        args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
        if result.returncode or not report.get('passed'):raise SystemExit(1)


if __name__=='__main__':main()

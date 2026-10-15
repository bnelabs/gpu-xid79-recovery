#!/usr/bin/env python3
"""Boot an isolated eight-endpoint QEMU campaign; no host passthrough/network."""
import argparse
import gzip
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile

from run import build_root, copy_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=pathlib.Path)
    parser.add_argument('--arch', choices=['x86_64', 'aarch64'], default='x86_64')
    parser.add_argument('--userspace', type=pathlib.Path)
    parser.add_argument('--storage-shared', action='store_true', help='Place NVMe inside the proposed GPU reset scope; must refuse')
    parser.add_argument('--output', required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.arch == 'aarch64' and not args.userspace:
        parser.error('ARM64 requires matching userspace')
    repo = pathlib.Path(__file__).resolve().parents[2]
    lab = pathlib.Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='enterprise-qemu-') as directory:
        base = pathlib.Path(directory)
        root = base/'root'
        root.mkdir()
        build_root(root, args.userspace)
        (root/'run').chmod(0o755)
        init = root/'init'
        init.write_text(init.read_text().replace('/usr/bin/python3 /lab/guest.py', '/bin/busybox ip link set lo up\n/usr/bin/python3 /lab/guest.py'))
        for name in ('enterprise_guest.py', 'enterprise_fixture.py'):
            copy_file(lab/name, root, 'lab/'+name)
        copy_file(lab/'enterprise_guest.py', root, 'lab/guest.py')
        copy_file(repo/'domain_adapter.py', root, 'lab/domain_adapter.py')
        copy_file(repo/'adapters/redfish_gpu.py', root, 'lab/adapters/redfish_gpu.py')
        copy_file(repo/'adapters/reset_journal.py', root, 'lab/adapters/reset_journal.py')
        (root/'lab/adapters').chmod(0o755)
        for path in (root/'lab').rglob('*.py'):
            path.chmod(0o644)
        stub = root/'usr/bin/systemctl'
        stub.write_text('''#!/bin/sh
case "$1" in
is-active) if [ -f "/run/enterprise-services/$2" ]; then echo active; exit 0; else echo inactive; exit 3; fi;;
stop) /bin/busybox rm -f "/run/enterprise-services/$2";;
start) /bin/busybox touch "/run/enterprise-services/$2";;
*) exit 1;;
esac
/bin/busybox printf '{"kind":"service","verb":"%s","unit":"%s"}\\n' "$1" "$2" >> /run/enterprise-events.jsonl
''')
        stub.chmod(0o755)
        names = ['.', *[str(p.relative_to(root)) for p in sorted(root.rglob('*'))]]
        packed = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--owner=0:0'], cwd=root,
                                input=('\0'.join(names)+'\0').encode(), capture_output=True, check=True)
        initrd = base/'initrd.gz'
        initrd.write_bytes(gzip.compress(packed.stdout, compresslevel=1))
        x86 = args.arch == 'x86_64'
        command = [f'qemu-system-{args.arch}', '-machine', 'q35' if x86 else 'virt',
            '-accel', 'tcg', '-cpu', 'max' if x86 else 'cortex-a57', '-smp', '2', '-m', '1024',
            '-nodefaults', '-no-user-config', '-display', 'none', '-serial', 'stdio',
            '-monitor', 'none', '-nic', 'none', '-no-reboot', '-kernel', str(args.kernel.resolve()),
            '-initrd', str(initrd), '-append',
            f'console={"ttyS0" if x86 else "ttyAMA0"} panic=-1 quiet gpu_recovery_lab=1'
            + (' lab_storage_shared=1' if args.storage_shared else '')]
        for group in range(4):
            command += ['-device', f'pcie-root-port,id=rp{group},bus=pcie.0,addr={group+2},chassis={group+1}',
                        '-device', f'pci-bridge,id=pair{group},bus=rp{group},addr=0,chassis_nr={group+10}']
            for member in range(2):
                command += ['-device', f'pci-testdev,id=gpu{group*2+member},bus=pair{group},addr={member+1}']
        if args.storage_shared:
            storage_bus = 'pair0,addr=3'
        else:
            command += ['-device', 'pcie-root-port,id=storage,bus=pcie.0,addr=6,chassis=5']
            storage_bus = 'storage,addr=0'
        command += ['-device', 'nvme,id=nvme,serial=LAB-STORAGE,bus='+storage_bus]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        console = args.output.with_suffix('.console.log')
        with console.open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                rc = process.wait(timeout=300)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        lines = [line for line in console.read_text().splitlines() if line.startswith('GPU_QEMU_RESULT=')]
        if not lines:
            raise RuntimeError('No guest result; inspect '+str(console))
        report = json.loads(lines[-1].split('=', 1)[1])
        report['qemu_returncode'] = rc
        report['qemu_version'] = subprocess.check_output([command[0], '-version'], text=True).splitlines()[0]
        report['inputs_sha256'] = {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (repo/'gpu_recovery.py', repo/'domain_adapter.py', repo/'adapters/redfish_gpu.py', repo/'adapters/reset_journal.py',
                         lab/'enterprise_guest.py', lab/'enterprise_fixture.py', pathlib.Path(__file__).resolve())}
        report['kernel_sha256'] = hashlib.sha256(args.kernel.read_bytes()).hexdigest()
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'output': str(args.output), 'passed': report.get('passed'),
                          'cases': [{k: case[k] for k in ('fault', 'passed', 'error')} for case in report.get('cases', [])],
                          'error': report.get('error')}, indent=2))
        if rc or not report.get('passed'):
            raise SystemExit(1)


if __name__ == '__main__':
    main()

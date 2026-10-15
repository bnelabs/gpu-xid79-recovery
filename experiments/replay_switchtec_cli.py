#!/usr/bin/env python3
"""Build pinned vendor CLI; replace all management access at link time.

No PCI, UART or I2C device is opened. Not a switch firmware emulator.
"""
import argparse
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile
import tempfile

REVISION = '99da3bc799b84ff53abc726c2fdea01379b3c687'
WRAPPER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <switchtec/switchtec.h>
struct switchtec_dev *__wrap_switchtec_open(const char *path) {
    if (strcmp(path, "LAB-ONLY")) { errno = EPERM; return NULL; }
    return (struct switchtec_dev *)0x1000;
}
void __wrap_switchtec_close(struct switchtec_dev *dev) {}
enum switchtec_gen __wrap_switchtec_gen(struct switchtec_dev *dev) { return SWITCHTEC_GEN4; }
enum switchtec_variant __wrap_switchtec_variant(struct switchtec_dev *dev) { return SWITCHTEC_PAX; }
int __wrap_switchtec_set_pax_id(struct switchtec_dev *dev, int id) { return 0; }
int __wrap_switchtec_cmd(struct switchtec_dev *dev, uint32_t cmd,
    const void *payload, size_t len, void *resp, size_t rlen) {
    const unsigned char *p = payload;
    if (cmd != 0x8d || len != 4 || rlen != 0) { exit(91); }
    /* Reserved fourth byte is not initialized by this vendor revision. */
    printf("{\"command\":%u,\"control\":%u,\"port\":%u,\"flag\":%u,\"length\":%zu}\n",
        cmd, p[0], p[1], p[2], len);
    if (getenv("LAB_FAIL")) { errno = EIO; return -1; }
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vendor-repo', required=True, type=pathlib.Path)
    p.add_argument('--output', required=True, type=pathlib.Path)
    args = p.parse_args()
    archive = subprocess.check_output(['git', '-C', str(args.vendor_repo), 'archive', REVISION])
    with tempfile.TemporaryDirectory(prefix='switchtec-cli-replay-') as directory:
        root = pathlib.Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(root, filter='data')
        log = args.output.with_suffix('.build.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w') as out:
            subprocess.run(['./configure', '--without-ncurses', '--without-openssl'], cwd=root,
                           stdout=out, stderr=subprocess.STDOUT, check=True)
            subprocess.run(['make', '-j4', 'switchtec'], cwd=root, stdout=out,
                           stderr=subprocess.STDOUT, check=True)
            (root/'wrapper.c').write_text(WRAPPER)
            symbols = ['switchtec_open', 'switchtec_close', 'switchtec_gen',
                       'switchtec_variant', 'switchtec_set_pax_id', 'switchtec_cmd']
            subprocess.run(['gcc', '-Iinc', 'wrapper.c', *map(str, sorted((root/'build/cli').glob('*.o'))),
                'libswitchtec.a', '-lm', '-Wl,'+','.join('--wrap='+s for s in symbols),
                '-o', 'lab-cli'], cwd=root, stdout=out, stderr=subprocess.STDOUT, check=True)
        cases = []
        for port in range(256):
            for flag, code in [('SET', 1), ('CLEAR', 0)]:
                result = subprocess.run([str(root/'lab-cli'), 'fabric', 'port_control', 'LAB-ONLY',
                    '--phys_port_id', str(port), '--control_type', 'HOT_RESET',
                    '--hot_reset_flag', flag], capture_output=True, text=True, timeout=5)
                observed = json.loads(result.stdout)
                expected = dict(command=0x8d, control=3, port=port, flag=code, length=4)
                cases.append({'port': port, 'flag': flag, 'passed': result.returncode == 0 and observed == expected})
        # Confirm library failures reach the CLI caller; no simulated retry.
        import os
        failed = subprocess.run([str(root/'lab-cli'), 'fabric', 'port_control', 'LAB-ONLY',
            '-p', '8', '-t', 'HOT_RESET', '-f', 'SET'], env=dict(os.environ, LAB_FAIL='1'),
            capture_output=True, text=True, timeout=5)
        failure_propagated = failed.returncode != 0 and len(failed.stdout.splitlines()) == 1
        report = {'schema': 1, 'vendor_revision': REVISION, 'cases': cases,
            'failure_propagated': failure_propagated,
            'passed': all(c['passed'] for c in cases) and failure_propagated,
            'hardware_access': False, 'switch_firmware_emulated': False,
            'reserved_payload_byte_checked': False,
            'experiment_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
            'adapter_sha256': hashlib.sha256((pathlib.Path(__file__).resolve().parents[1]/'adapters/switchtec_gpu.py').read_bytes()).hexdigest()}
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'cases'}))
        if not report['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()

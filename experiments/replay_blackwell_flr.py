#!/usr/bin/env python3
"""Compile actual GB100 FLR control flow against offline config/clock models."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

SOURCE = 'src/nvidia/src/kernel/gpu/bif/arch/blackwell/kernel_bif_gb100.c'
SHA256 = '6063e9e91bfd933f676f7677dd6c86631e4945c00c72f70b40027bf26fe8edcd'
PREFIX = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
typedef uint32_t NvU32;
typedef int NV_STATUS;
typedef struct {int unused;} OBJGPU;
typedef struct {int unused;} KernelBif;
typedef struct {int checks;} RMTIMEOUT;
#define NV_OK 0
#define NV_ERR_TIMEOUT 7
#define NV_TRUE 1
#define NV_ASSERT_FAILED(...) ((void)0)
/* Register addresses and field operations are models, not hardware headers. */
#define NV_PF0_DEVICE_CONTROL_AND_STATUS 1
#define NV_PF0_DEVICE_VENDOR_ID 2
#define FLD_SET_DRF_NUM(a,b,c,d,v) ((v) | (1U << 15))
#define FLD_TEST_DRF(a,b,c,d,v) (((v) & 0xffffU) == 0x10deU)
static int reads, writes, delays, read_status, write_status, ready_at;
static NvU32 original, written;
static int rd(OBJGPU *g, int reg, NvU32 *v) {
 (void)g; reads++;
 *v = reg == 1 ? original : (reads >= ready_at ? 0x290110deU : 0xffffffffU);
 return read_status;
}
static int wr(OBJGPU *g, int reg, NvU32 v) {
 (void)g; assert(reg == 1); writes++; written=v; return write_status;
}
#define GPU_BUS_CFG_CYCLE_RD32 rd
#define GPU_BUS_CFG_CYCLE_WR32 wr
static int gpuCheckTimeout(OBJGPU *g, RMTIMEOUT *t) {
 (void)g; return ++t->checks >= 5 ? NV_ERR_TIMEOUT : NV_OK;
}
static void osDelay(int ms) {assert(ms == 1); delays++;}
static void clear(void) {
 reads=writes=delays=read_status=write_status=0; ready_at=1;
 original=0x12340042U; written=0;
}
'''
SUFFIX = r'''
int main(void) {
 OBJGPU g={0}; KernelBif b={0}; RMTIMEOUT t={0};
 clear(); read_status=3;
 assert(kbifTriggerFlr_GB100(&g,&b)==3 && reads==1 && writes==0);
 clear(); write_status=4;
 assert(kbifTriggerFlr_GB100(&g,&b)==4 && reads==1 && writes==1);
 clear(); assert(kbifTriggerFlr_GB100(&g,&b)==NV_OK);
 assert(reads==1 && writes==1 && written==(original | (1U<<15)));
 clear(); assert(kbifConfigAccessWait_GB100(&g,&b,&t)==NV_OK);
 assert(reads==1 && delays==0 && writes==0);
 clear(); ready_at=4; t.checks=0;
 assert(kbifConfigAccessWait_GB100(&g,&b,&t)==NV_OK);
 assert(reads==4 && delays==3 && writes==0);
 clear(); ready_at=100; t.checks=0;
 assert(kbifConfigAccessWait_GB100(&g,&b,&t)==NV_ERR_TIMEOUT);
 assert(reads==5 && delays==4 && writes==0);
 clear(); read_status=3; t.checks=0;
 assert(kbifConfigAccessWait_GB100(&g,&b,&t)==NV_ERR_TIMEOUT);
 assert(reads==5 && delays==4 && writes==0);
 puts("{\"cases_passed\":7,\"unreachable_trigger_writes\":0,\"permanent_unreachable_wait\":\"timeout\"}");
 return 0;
}
'''

def extract(source, name):
    start = re.search(r'NV_STATUS\s*\n' + re.escape(name) + r'\s*\(', source)
    if not start:
        raise RuntimeError('Missing function: ' + name)
    brace = source.index('{', start.start())
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start.start():end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    data = (args.checkout / SOURCE).read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Unreviewed upstream input')
    functions = '\n'.join(extract(data.decode(), name) for name in
                          ('kbifTriggerFlr_GB100', 'kbifConfigAccessWait_GB100'))
    with tempfile.TemporaryDirectory(prefix='blackwell-flr-') as directory:
        base = pathlib.Path(directory)
        (base/'test.c').write_text(PREFIX + functions + SUFFIX)
        subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(base/'test.c'), '-o', str(base/'test')], check=True)
        result = subprocess.run([str(base/'test')], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr)
        report = json.loads(result.stdout)
    report.update(kind='Actual upstream C control-flow replay with modeled registers, fields, config transport and clock',
                  upstream_commit='ca2d03e6fafd4d4b56b383497d931c25b283bfcf',
                  input_sha256=SHA256, hardware_recovery_verified=False)
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()

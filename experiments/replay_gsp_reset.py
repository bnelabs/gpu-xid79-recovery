#!/usr/bin/env python3
"""Offline fault replay of actual upstream GSP reset bodies, not GPU emulation."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile
from replay_blackwell_flr import extract

ROOT = 'src/nvidia/src/kernel/gpu/gsp/arch/'
INPUTS = [
 ('turing/kernel_gsp_tu102.c', 'TU102', 'df312262254d712e8340c2d428bb5869354756f41b7a5758aac2f1825fdf2550'),
 ('hopper/kernel_gsp_gh100.c', 'GH100', '5ee22f09c650ab70764be05828cc58e1237436bada08c91898aeadc011d37ea2'),
 ('blackwell/kernel_gsp_gb100.c', 'GB100', 'cd92274258afcabd32a784a5bda539b7550d6c9a79019ad8bca2964a4769efd0'),
]
MODEL = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
typedef uint32_t NvU32;
typedef int NV_STATUS;
typedef int NvBool;
typedef struct {struct {int defaultResetFSMStateTransitionUs;} timeoutData;} OBJGPU;
typedef struct {int unused;} KernelGsp;
typedef struct {int unused;} RMTIMEOUT;
#define NV_OK 0
#define GPU_TIMEOUT_DEFAULT 100
#define GPU_TIMEOUT_FLAGS_TMR 1
#define NV_PRINTF(...) ((void)0)
/* Release-style non-halting breakpoint; not a model of debug builds. */
static int breaks, writes, reads, waits, timeouts;
static int outcomes[3], last_outcome, transitions[2];
static NvU32 register_value;
#define DBG_BREAKPOINT() (++breaks)
#define FLCN_RESET_PROPAGATION_DELAY_COUNT 10
#define NV_PGSP_FALCON_ENGINE 1
#define _ASSERT 1
#define _DEASSERT 0
#define _TRUE 1
#define _FALSE 0
static void write_reset(int value) {assert(writes<2); transitions[writes++]=value;}
#define GPU_FLD_WR_DRF_DEF(g,a,b,c,d) write_reset(d)
static NvU32 read_reg(void) {reads++; return register_value;}
#define GPU_REG_RD32(g,r) read_reg()
/* Polling predicates are represented by prescribed wait outcomes. */
static NvBool _kgspWaitForAsserted(OBJGPU *g,void *p) {return 0;}
static NvBool _kgspWaitForDeasserted(OBJGPU *g,void *p) {return 0;}
static NvBool _kgspWaitForResetAccess(OBJGPU *g,void *p) {return 0;}
static void gpuSetTimeout(OBJGPU *g,int us,RMTIMEOUT *t,int flags) {timeouts++;}
static int gpuTimeoutCondWait(OBJGPU *g,NvBool (*predicate)(OBJGPU *,void *),void *p,RMTIMEOUT *t) {
 assert(waits<3); last_outcome=outcomes[waits++]; return last_outcome;
}
static void clear(void) {breaks=writes=reads=waits=timeouts=last_outcome=0;}
'''
MAIN = r'''
int main(void) {
 OBJGPU g={{10}}; KernelGsp k={0};
 for(int lost=0;lost<2;lost++) {
  clear(); register_value=lost ? 0xffffffffU : 0;
  assert(kgspResetHw_TU102(&g,&k)==NV_OK);
  assert(reads==20 && writes==2 && transitions[0]==1 && transitions[1]==0);
 }
 /* Exhaust all success/timeout combinations, retaining distinct error values. */
 for(int a=0;a<2;a++) for(int d=0;d<2;d++) {
  clear(); outcomes[0]=a ? 7 : NV_OK; outcomes[1]=d ? 8 : NV_OK;
  assert(kgspResetHw_GH100(&g,&k)==outcomes[1]);
  assert(waits==2 && timeouts==2 && writes==2 && breaks==a+d);
  assert(transitions[0]==1 && transitions[1]==0);
 }
 for(int p=0;p<2;p++) for(int a=0;a<2;a++) for(int d=0;d<2;d++) {
  clear(); outcomes[0]=p ? 6 : NV_OK; outcomes[1]=a ? 7 : NV_OK; outcomes[2]=d ? 8 : NV_OK;
  assert(kgspResetHw_GB100(&g,&k)==outcomes[2]);
  assert(waits==3 && timeouts==3 && writes==2 && breaks==p+a+d);
  assert(transitions[0]==1 && transitions[1]==0);
 }
 puts("{\"cases_passed\":14,\"tu102_all_ones_returns_success\":true,\"gh100_assert_failure_can_be_overwritten\":true,\"gb100_access_timeout_continues_to_reset\":true,\"gh100_gb100_return_final_deassert_wait_status\":true}");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    functions, inputs = [], []
    for relative, chip, expected in INPUTS:
        path = ROOT + relative
        data = (args.checkout / path).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise RuntimeError('Unreviewed upstream input: ' + path)
        functions.append(extract(data.decode(), 'kgspResetHw_' + chip))
        inputs.append(dict(path=path, sha256=actual))
    with tempfile.TemporaryDirectory(prefix='gsp-reset-') as directory:
        base = pathlib.Path(directory)
        (base/'test.c').write_text(MODEL + '\n'.join(functions) + MAIN)
        subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(base/'test.c'), '-o', str(base/'test')], check=True)
        result = subprocess.run([str(base/'test')], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr)
        report = json.loads(result.stdout)
    report.update(upstream_commit='ca2d03e6fafd4d4b56b383497d931c25b283bfcf', inputs=inputs,
                  kind='Actual C reset bodies with prescribed wait outcomes and modeled register IO',
                  breakpoint_model='Non-halting release-style behavior; counted for assertions',
                  predicates_executed=False, firmware_executed=False, hardware_recovery_verified=False,
                  complete_driver_executed=False)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()

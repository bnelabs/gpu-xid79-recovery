#!/usr/bin/env python3
"""Replay selected upstream FLR functions; never access hardware or load a driver."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

from replay_blackwell_flr import PREFIX, SOURCE, SHA256, extract

INPUTS = [
    ('turing/kernel_bif_tu102.c', 'TU102', 'deb0b0aa06023fd73e233e6c727e2fb2a2574ed764b990239605ee18a910a289'),
    ('hopper/kernel_bif_gh100.c', 'GH100', 'c1dd061c19f133145607e1576e9664ecfbab095640c63a596cfc7ad0e3c4b15f'),
    ('blackwell/kernel_bif_gb100.c', 'GB100', SHA256),
]
MODEL = r'''
typedef uint8_t NvU8;
typedef uint16_t NvU16;
typedef struct {int unused;} OBJSYS;
typedef struct {int unused;} OBJCL;
#define gpuGetDomain(g) 0
#define gpuGetBus(g) 1
#define gpuGetDevice(g) 0
#define SYS_GET_INSTANCE() ((OBJSYS *)0)
#define SYS_GET_CL(s) ((OBJCL *)0)
#define NV_XVE_DEVICE_CONTROL_STATUS 1
#define NV_EP_PCFG_GPU_DEVICE_CONTROL_STATUS 1
static void *osPciInitHandle(int d,int b,int f,int n,NvU16 *v,NvU16 *id) {
 *v=0x10de; *id=0; return (void *)1;
}
static NvU32 osPciReadDword(void *h,int reg) {
 NvU32 value; rd(0,reg,&value); return value;
}
static void clPcieWriteDword(OBJCL *c,int d,int b,int f,int n,int reg,NvU32 v) {
 wr(0,reg,v);
}
'''
MAIN = r'''
int main(void) {
 OBJGPU g={0}; KernelBif b={0};
 /* TU102 exposes neither read nor write status at this call boundary. */
 clear(); assert(kbifTriggerFlr_TU102(&g,&b)==NV_OK);
 assert(reads==1 && writes==1 && written==(original | (1U<<15)));
 clear(); original=0xffffffffU; read_status=3;
 assert(kbifTriggerFlr_TU102(&g,&b)==NV_OK && writes==1 && written==0xffffffffU);
 clear(); write_status=4;
 assert(kbifTriggerFlr_TU102(&g,&b)==NV_OK && writes==1);
 NV_STATUS (*functions[])(OBJGPU *,KernelBif *)={kbifTriggerFlr_GH100,kbifTriggerFlr_GB100};
 for(int i=0;i<2;i++) {
  clear(); read_status=3;
  assert(functions[i](&g,&b)==3 && reads==1 && writes==0);
  clear(); write_status=4;
  assert(functions[i](&g,&b)==4 && writes==1);
  clear(); assert(functions[i](&g,&b)==NV_OK && written==(original | (1U<<15)));
  /* Successful transport carrying all ones is distinct from transport error. */
  clear(); original=0xffffffffU;
  assert(functions[i](&g,&b)==NV_OK && writes==1 && written==0xffffffffU);
 }
 puts("{\"cases_passed\":11,\"tu102_transport_status_propagated\":false,\"gh100_gb100_transport_error_propagated\":true,\"all_ones_success_requires_external_validation\":true}");
}
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=pathlib.Path)
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    functions, inputs = [], []
    for relative, chip, expected in INPUTS:
        path = pathlib.Path(SOURCE).parents[1] / relative
        data = (args.checkout / path).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise RuntimeError(f'Unreviewed upstream input: {path}')
        functions.append(extract(data.decode(), 'kbifTriggerFlr_' + chip))
        inputs.append(dict(path=str(path), sha256=actual, function='kbifTriggerFlr_' + chip))
    with tempfile.TemporaryDirectory(prefix='generation-flr-') as directory:
        base = pathlib.Path(directory)
        source = base / 'test.c'
        source.write_text(PREFIX + MODEL + '\n'.join(functions) + MAIN)
        subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(source), '-o', str(base/'test')], check=True)
        result = subprocess.run([str(base/'test')], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr)
        report = json.loads(result.stdout)
    report.update(upstream_commit='ca2d03e6fafd4d4b56b383497d931c25b283bfcf', inputs=inputs,
                  kind='Actual upstream function bodies with modeled transport and register fields',
                  scope='Selected TU102/GH100/GB100 functions only; HAL dispatch and complete driver not executed',
                  hardware_recovery_verified=False, firmware_emulated=False)
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(output, end='')

if __name__ == '__main__':
    main()

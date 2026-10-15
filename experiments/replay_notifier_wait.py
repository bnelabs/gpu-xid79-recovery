#!/usr/bin/env python3
"""Compile NVIDIA's notifier wait with modeled MMIO/time, plus a lab candidate.

No kernel modules are built or loaded. This tests one C function, not complete
GPU teardown, firmware, PCI hardware, or recovery.
"""
import argparse,hashlib,json,pathlib,subprocess,tempfile

SHA256='269f5b2b0f00f88809eb9d1228f3ed172507749bc07e399ee442494fc4875b16'
PREFIX=r'''
#include <stdint.h>
#include <stdio.h>
#include <setjmp.h>
typedef uint32_t NvU32;
typedef uint64_t NvU64;
typedef int NvBool;
#define TRUE 1
#define FALSE 0
/* Every scenario uses a one-bit notifier at base/extent bit zero. */
#define DRF_SHIFTMASK(range) 1U
#define NV_EVO_NOTIFIER_SHORT_TIMEOUT_USEC 3
#define EVO_LOG_WARN 0
#define nvAssert(x) ((void)0)
#define nvEvoLogDisp(...) ((void)0)
typedef struct { NvU32 put_offset; } Dma;
typedef Dma *NVDmaBufferEvoPtr;
typedef struct { Dma pb; struct { volatile NvU32 *cpuAddress; } notifierDma; } Core;
typedef struct { Core *core; } Dev;
typedef Dev *NVDevEvoPtr;
typedef struct { NvU32 displayOwner; Dev *pDevEvo; } NVDispEvoRec;
static int tick, mode, writes;
static jmp_buf budget;
static NvU32 notifier;
static NvU32 EvoCoreReadGet(Dma *p) {
 if (mode==1) return 0xffffffff;
 if (mode==2) return p->put_offset;
 return p->put_offset-4;
}
static int nvExceedsTimeoutUSec(Dev *p, NvU64 *start, int timeout) {
 (void)p; (void)start; return tick>=timeout;
}
static void EvoWriteNotifier(volatile NvU32 *p,NvU32 value) { writes++; *p=value; }
static void nvkms_yield(void) {
 if (++tick>20) longjmp(budget,1);
 if (mode==3 && tick==5) notifier=1;
}
'''
SUFFIX=r'''
int main(void) {
 const char *names[]={"completed","nonblocking","lost_drained_notification","slow_reachable","lost_all_ones","stalled_reachable"};
 int modes[]={0,0,2,3,1,0};
 printf("[");
 for (int variant=0;variant<2;variant++) for(int test=0;test<6;test++) {
  Core core={.pb={.put_offset=16},.notifierDma={.cpuAddress=&notifier}};
  Dev dev={.core=&core}; NVDispEvoRec disp={.displayOwner=0,.pDevEvo=&dev};
  tick=0;writes=0;mode=modes[test];notifier=test==0?1:0;
  int escaped=setjmp(budget), result=-1;
  if (!escaped) result=variant?CandidateEvoCheckNotifier(&disp,0,0,0,1,test!=1):EvoCheckNotifier(&disp,0,0,0,1,test!=1);
  int expected=(test==1)?0:1;
  if(test==4)expected=variant?0:-1;
  if(test==5)expected=-1;
  int passed=result==expected;
  if(test==4 && variant)passed=passed && notifier==0 && writes==0;
  printf("%s{\"variant\":\"%s\",\"case\":\"%s\",\"returned\":%d,\"step_budget\":%s,\"ticks\":%d,\"notifier_writes\":%d,\"matched_expected\":%s}",variant||test?",":"",variant?"candidate":"original",names[test],result,escaped?"true":"false",tick,writes,passed?"true":"false");
 }
 puts("]");return 0;
}
'''


def extract(path):
 source=path.read_text();start=source.index('static NvBool EvoCheckNotifier(')
 brace=source.index('{',start);depth=1;end=brace+1
 while depth:
  if source[end]=='{':depth+=1
  elif source[end]=='}':depth-=1
  end+=1
 function=source[start:end]
 if hashlib.sha256(function.encode()).hexdigest()!=SHA256:raise ValueError('Unreviewed notifier function; source hash mismatch')
 return source[:source.index('*/')+2],function


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('upstream',type=pathlib.Path)
 parser.add_argument('--cc',default='cc');args=parser.parse_args()
 license_text,original=extract(args.upstream/'src/nvidia-modeset/src/nvkms-dma.c')
 marker='        if (nvExceedsTimeoutUSec(\n'
 if original.count(marker)!=1:raise ValueError('Candidate insertion point changed')
 candidate=original.replace('EvoCheckNotifier(','CandidateEvoCheckNotifier(',1).replace(marker,
 '''        if (nvExceedsTimeoutUSec(pDevEvo, &startTime,
                                 NV_EVO_NOTIFIER_SHORT_TIMEOUT_USEC) &&
            EvoCoreReadGet(p) == 0xffffffffU) {
            return FALSE;
        }

'''+marker,1)
 with tempfile.TemporaryDirectory(prefix='notifier-replay-') as directory:
  root=pathlib.Path(directory);c=root/'replay.c';executable=root/'replay'
  c.write_text(license_text+'\n'+PREFIX+'\n'+original+'\n'+candidate+'\n'+SUFFIX)
  subprocess.run([args.cc,'-std=c11','-O0','-Wall','-Wextra','-Wno-unused-variable',str(c),'-o',str(executable)],check=True,capture_output=True,timeout=30)
  result=subprocess.run([str(executable)],check=True,capture_output=True,text=True,timeout=10)
  cases=json.loads(result.stdout)
 report={'kind':'Actual C function with stubbed hardware/time; not hardware emulation or a deployable driver fix',
         'upstream_commit':'ca2d03e6fafd4d4b56b383497d931c25b283bfcf',
         'function_sha256':SHA256,'cases':cases,'passed':sum(c['matched_expected'] for c in cases),
         'limits':['Model uses one notifier bit, synthetic time and GET values',
                   'Outer nvEvoWaitForCoreNotifier returns void and ignores failure; full error propagation is unresolved',
                   'Candidate does not address nvEvoMakeRoom, other waits, driver lifecycle or a missing physical reset path',
                   'Reachable but stalled GET remains unbounded in both variants',
                   'No kernel module was built, patched or loaded']}
 print(json.dumps(report,indent=2))
 if report['passed']!=len(cases):raise SystemExit(1)

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Compile the rebased lost-device FIFO methods against a bounded host model.
No kernel/GPU access. Header encoding, MMIO, event delivery and time are modeled.
"""
import argparse,hashlib,json,pathlib,re,subprocess,tempfile
EXPECTED={
 'nvEvoSetDeviceLost':'4bd5313b9458b72e3d5c0e4ccb624310bd0732b5ddc0eddd53ec02c66c9c870a',
 'nvEvoDeviceIsLost':'aa40010cb246635f7722e355360dad34d0009fb932204f46e4e737dcc50b8808',
 'EvoResetPushBufferForLostGpu':'4810a8912424b85bc44dc99198cf72c5942225f5451bd1bcddcc43bf0b075dc7',
 'nvEvoMakeRoom':'8b60b9ec250ec4bf5714fb6ab2a359f3aa4da434fa157b8cb6e36e0a8db32028',
 'nvDmaSetEvoMethodData':'27a4696c205dc34c9b20ed32519a80cdd4a89ec2589604fa80e38170d4356614',
 'EvoCheckNotifier':'d7809efc33d584cd51c6d7aad63a2f0ecfa0ca0086e3ed681359cd07953431e5',
 'nvDmaSetStartEvoMethod':'e10313d7fe7bc4d74e8febfceb712ff9a5b0db04bfb2eee8eb41aff07b0f19bd'}
PREFIX=r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
typedef uint32_t NvU32; typedef uint64_t NvU64; typedef int NvBool;
#define TRUE 1
#define FALSE 0
#define nvAssert(x) assert(x)
#define NV_DMA_PUSHER_CHASE_PAD 5
#define NV_EVO_GPU_LOST_BACKSTOP_USEC 30000000
#define EVO_LOG_ERROR 0
#define EVO_LOG_WARN 1
#define NV_EVO_NOTIFIER_SHORT_TIMEOUT_USEC 3000000
#define DRF_SHIFTMASK(range) 1U
#define nvEvoLogDisp(...) ((void)0)
#define nvEvoLogDev(...) ((void)0)
/* Actual header bit encoding is not relevant to this ring-bounds model. */
#define ASSERT_DRF_NUM(...) ((void)0)
#define DRF_DEF(...) 0U
#define DRF_NUM(...) 0U
typedef struct Dev NVDevEvoRec, *NVDevEvoPtr;
typedef struct { NvU32 *base, *buffer; NvU32 offset_max, put_offset, fifo_free_count; NVDevEvoPtr pDevEvo; } Dma, *NVDmaBufferEvoPtr;
typedef struct { Dma pb; NvU32 hwclass, instance; } Channel, *NVEvoChannelPtr;
typedef struct { Dma pb; struct { volatile NvU32 *cpuAddress; } notifierDma; } Core;
struct Dev { NvU32 gpuLost; Core *core; };
typedef struct { NvU32 displayOwner; NVDevEvoPtr pDevEvo; } NVDispEvoRec;
static NvU64 tick; static int mode, notifier_writes; static NVDevEvoPtr active_dev;
static NvU32 notifier;
static void EvoWriteNotifier(volatile NvU32 *p,NvU32 value) { notifier_writes++;*p=value; }
static NvU32 EvoCoreReadGet(Dma *p) {
 if(mode==8)return p->put_offset;
 if (mode==1 || mode==6) return 0xffffffffU;
 if (mode==2) return tick>=61000000?216:20+(tick/5000000)%4;
 if (mode==3) return 128;
 return 20;
}
static int nvExceedsTimeoutUSec(NVDevEvoPtr dev,NvU64 *start,NvU64 timeout) {
 (void)dev;
 if (*start==0) { *start=tick; return FALSE; }
 return tick-*start>=timeout;
}
static void nvkms_yield(void) {
 tick+=1000000;
 if(mode==2 && tick>=61000000)notifier=1;
 if(mode==4 && tick>=5000000)__atomic_store_n(&active_dev->gpuLost,1,__ATOMIC_RELEASE);
 if(tick>200000000)exit(3);
}
static void nvDmaKickoffEvo(NVEvoChannelPtr channel) {
 channel->pb.put_offset=(NvU32)((char *)channel->pb.buffer-(char *)channel->pb.base);
}
'''
SUFFIX=r'''
struct Ring { NvU32 words[65]; NvU32 guard; };
static void check_ring(struct Ring *ring,Dma *p) {
 assert(p->buffer>=p->base && p->buffer<=p->base+64);
 assert(p->fifo_free_count<=64);
 assert(ring->guard==0xcafefeedU);
}
int main(void) {
 const char *names[]={"healthy_space","known_lost_repeated_methods","late_loss_event","all_ones_stalled_ring","reachable_stalled_ring","slow_progress_beyond_30s","all_ones_with_free_space"};
 int modes[]={3,0,4,1,0,2,6};
 printf("[");
 for(int test=0;test<7;test++) {
  struct Ring ring={.guard=0xcafefeedU};NVDevEvoRec dev={.gpuLost=test==1};
  Channel channel={.pb={.base=ring.words,.buffer=ring.words+4,.offset_max=256,.put_offset=16,.fifo_free_count=0,.pDevEvo=&dev}};
  Dma *p=&channel.pb;mode=modes[test];tick=1;active_dev=&dev;
  if(test==3){p->buffer=p->base+60;p->put_offset=240;}
  if(test==1) {
   for(NvU32 i=0;i<100000;i++) {
    NvU32 count=1+i%63;
    nvDmaSetStartEvoMethod(&channel,0,count);check_ring(&ring,p);
    for(NvU32 j=0;j<count;j++){nvDmaSetEvoMethodData(&channel,j);check_ring(&ring,p);}
   }
  } else {nvEvoMakeRoom(&channel,test==0||test==6?8:32);check_ring(&ring,p);}
  int expected_lost=test==1||test==2||test==3||test==4||test==6;
  int passed=(dev.gpuLost!=0)==expected_lost;
  if(test==5)passed=passed && tick>=61000000;
  printf("%s{\"case\":\"%s\",\"matched_expected\":%s,\"lost_flag\":%s,\"simulated_usec\":%llu,\"buffer_dword\":%ld,\"free_dwords\":%u,\"guard_intact\":true}",test?",":"",names[test],passed?"true":"false",dev.gpuLost?"true":"false",(unsigned long long)tick,(long)(p->buffer-p->base),p->fifo_free_count);
 }
 const char *notifier_names[]={"notifier_completed","notifier_known_lost","notifier_all_ones","notifier_stalled","notifier_slow_progress","notifier_drained","notifier_nonblocking","notifier_late_event"};
 int notifier_modes[]={0,0,1,0,2,8,1,4};
 for(int test=0;test<8;test++) {
  NVDevEvoRec dev={.gpuLost=test==1};Core core={.pb={.put_offset=16,.pDevEvo=&dev},.notifierDma={.cpuAddress=&notifier}};
  dev.core=&core;NVDispEvoRec disp={.displayOwner=0,.pDevEvo=&dev};
  notifier=test==0;notifier_writes=0;tick=1;mode=notifier_modes[test];active_dev=&dev;
  int result=EvoCheckNotifier(&disp,0,0,0,1,test!=6);
  int expected=test==0||test==4||test==5;
  int expected_lost=test==1||test==2||test==3||test==7;
  int passed=result==expected && (dev.gpuLost!=0)==expected_lost && notifier_writes==(test==5);
  printf(",{\"case\":\"%s\",\"matched_expected\":%s,\"returned\":%d,\"lost_flag\":%s,\"notifier_writes\":%d,\"simulated_usec\":%llu}",notifier_names[test],passed?"true":"false",result,dev.gpuLost?"true":"false",notifier_writes,(unsigned long long)tick);
 }
 puts("]");return 0;
}
'''

def extract(source,name):
 match=re.search(r'^(?:(?:static|inline)\s+)*(?:void|NvBool)\s+'+re.escape(name)+r'\s*\(',source,re.M)
 start=match.start();brace=source.index('{',match.end());depth=1;end=brace+1
 while depth:
  if source[end]=='{':depth+=1
  elif source[end]=='}':depth-=1
  end+=1
 result=source[start:end]
 if hashlib.sha256(result.encode()).hexdigest()!=EXPECTED[name]:raise ValueError('Unreviewed method: '+name)
 return result


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('patched_source',type=pathlib.Path);args=parser.parse_args()
 source=(args.patched_source/'src/nvidia-modeset/src/nvkms-dma.c').read_text()
 header=(args.patched_source/'src/nvidia-modeset/include/nvkms-dma.h').read_text()
 methods=[extract(header if name.startswith('nvDmaSet') else source,name) for name in EXPECTED]
 license_text=source[:source.index('*/')+2]
 with tempfile.TemporaryDirectory(prefix='fifo-replay-') as directory:
  root=pathlib.Path(directory);c=root/'model.c';executable=root/'model'
  c.write_text(license_text+'\n'+PREFIX+'\n'+'\n'.join(methods)+'\n'+SUFFIX)
  subprocess.run(['cc','-std=c11','-O0','-Wall','-Wextra',str(c),'-o',str(executable)],check=True,capture_output=True,timeout=30)
  output=subprocess.run([str(executable)],check=True,capture_output=True,text=True,timeout=10)
  cases=json.loads(output.stdout)
 report={'kind':'Compiled patched methods with modeled hardware/time; no physical recovery',
         'method_sha256':EXPECTED,'cases':cases,'passed':sum(c['matched_expected'] for c in cases),
         'limits':['GPU event delivery, ring header bits, MMIO and time are modeled',
                   'Guard checks cover only the exercised packet sizes and ring capacity',
                   'The candidate treats a single all-ones GET as device loss; transient-read behavior needs hardware validation',
                   'No callback lifetime, concurrent teardown, DMA, firmware boot or CUDA execution is tested']}
 print(json.dumps(report,indent=2))
 if report['passed']!=len(cases):raise SystemExit(1)

if __name__=='__main__':main()

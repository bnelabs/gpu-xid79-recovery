#!/usr/bin/env python3
"""Compile actual OpenSMA reset admission/default-hook code without hardware I/O."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile

EXPECTED={
    'src/nv/mctp/nsm_type_2.cpp':'3ca7ec456abc90ba423f639d265651084ff38036aa72cbe55425f0d9a726580b',
    'src/nv/mctp/nsm_type_2.h':'a79a74ec911d06acef6f03ffabcb2fc75a13dacca6f6304f1be069c523af1069',
    'src/nv/mctp/enums.h':'4bfc3724b6720946f7c112f9f261e9b78893159ea09721197fd47806474e680b',
}


def block(source,marker):
    start=source.index(marker);opening=source.index('{',start);depth=1;end=opening+1
    while depth:
        if source[end]=='{':depth+=1
        elif source[end]=='}':depth-=1
        end+=1
    return source[start:end]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout',type=pathlib.Path)
    parser.add_argument('--output',type=pathlib.Path)
    args=parser.parse_args();sources={}
    for name,expected in EXPECTED.items():
        data=(args.checkout/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('Unreviewed input: '+name)
        sources[name]=data.decode()
    header=sources['src/nv/mctp/nsm_type_2.h'];code=sources['src/nv/mctp/nsm_type_2.cpp']
    enum=block(sources['src/nv/mctp/enums.h'],'enum class NsmStatus')+';'
    indexes=block(header,'enum PciLinksResetDevices')+';'
    array=header[header.index('constexpr auto PciLinkPcieResetValidDevicesSize'):header.index('static_assert(')]
    validate=block(code,'bool validatePcieLinkResetValue(')
    hook=block(code,'__attribute__((weak)) NsmStatus platform_assert_pcie_fundamental_reset(')
    harness='''#include <array>
#include <cstdint>
#include <cassert>
#include <cstdio>
namespace nv::mctp {
'''+enum+indexes+array+'namespace nsm_type2 {\n'+validate+hook+'''
}}
int main() {
 using namespace nv::mctp;
 unsigned admitted=0,rejected=0,unsupported=0;
 for(unsigned index=0;index<256;index++) {
  bool valid=nsm_type2::validatePcieLinkResetValue(index);
  if(valid)admitted++;else rejected++;
  for(unsigned action=0;action<2;action++)
   if(nsm_type2::platform_assert_pcie_fundamental_reset(index,action)==NsmStatus::NotSupported)unsupported++;
 }
 assert(admitted==11 && rejected==245 && unsupported==512);
 assert(!nsm_type2::validatePcieLinkResetValue(0));
 printf("{\\"admitted_indexes\\":%u,\\"rejected_indexes\\":%u,\\"default_hook_not_supported\\":%u,\\"index_zero_admitted\\":false,\\"allowed_indexes\\":[",admitted,rejected,unsupported);
 bool comma=false;
 for(unsigned index=0;index<256;index++)if(nsm_type2::validatePcieLinkResetValue(index)) {
  printf("%s%u",comma?",":"",index);comma=true;
 }
 printf("]}\\n");
}
'''
    with tempfile.TemporaryDirectory(prefix='opensma-replay-') as directory:
        base=pathlib.Path(directory);source=base/'test.cpp';source.write_text(harness);binary=base/'test'
        subprocess.run(['c++','-std=c++20','-O1','-fsanitize=address,undefined',str(source),'-o',str(binary)],check=True)
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError(result.stderr)
        report=json.loads(result.stdout)
    report.update({'kind':'Actual extracted OpenSMA firmware functions, offline host replay; not a full firmware emulator',
                   'upstream_commit':'0c220c3a2d1de8cdc93804f1d86892814ef8a1ca','input_sha256':EXPECTED,
                   'hardware_recovery_verified':False,
                   'limitations':'No platform strong override, packet dispatcher, MCU peripheral, I2C/I3C, CPLD, GPU, or firmware startup is executed.'})
    output=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(output)
    print(output,end='')


if __name__=='__main__':main()

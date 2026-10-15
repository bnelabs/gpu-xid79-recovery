#!/usr/bin/env python3
"""Inspect NVIDIA GSP ELF containers without loading or modifying firmware."""
import argparse,hashlib,json,pathlib,struct


def inspect_image(data,driver_version=None):
    if len(data)<64 or data[:7]!=b'\x7fELF\x02\x01\x01':
        raise ValueError('Expected a little-endian ELF64 container')
    machine=struct.unpack_from('<H',data,18)[0]
    offset=struct.unpack_from('<Q',data,40)[0]
    stride,count,names_index=struct.unpack_from('<HHH',data,58)
    if stride!=64 or not 0<count<=512 or names_index>=count or offset<64 or offset+count*stride>len(data):
        raise ValueError('Invalid or unsupported ELF section table')
    headers=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*stride) for i in range(count)]
    names_header=headers[names_index]
    def payload(header):
        begin,size=header[4:6]
        if begin>len(data) or size>len(data)-begin:raise ValueError('ELF section exceeds image bounds')
        return data[begin:begin+size]
    names=payload(names_header)
    sections={}
    for header in headers[1:]:
        if header[0]>=len(names):raise ValueError('Invalid section name offset')
        end=names.find(b'\0',header[0])
        if end<0 or end-header[0]>256:raise ValueError('Invalid section name')
        name=names[header[0]:end].decode('ascii')
        if name in sections:raise ValueError('Duplicate ELF section name')
        content=payload(header)
        sections[name]={'offset':header[4],'size':header[5],'sha256':hashlib.sha256(content).hexdigest()}
    if '.fwimage' not in sections or '.fwversion' not in sections:raise ValueError('GSP image/version sections missing')
    version=sections['.fwversion']
    if not 0<version['size']<=128:raise ValueError('Invalid firmware version size')
    version=data[version['offset']:version['offset']+version['size']].rstrip(b'\0').decode('ascii')
    families=sorted(name.removeprefix('.fwsignature_') for name in sections if name.startswith('.fwsignature_'))
    return {'format':'ELF64 NVIDIA GSP container','machine':machine,'bytes':len(data),
            'sha256':hashlib.sha256(data).hexdigest(),'firmware_version':version,
            'driver_version':driver_version,'version_matches_driver':version==driver_version if driver_version else None,
            'signature_sections':families,'sections':sections,
            'limits':['Signature sections are inventoried, not cryptographically verified',
                      'This file is not proof of which image a running GPU loaded',
                      'Family sections do not establish reset support or hardware recovery']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=pathlib.Path)
    parser.add_argument('--driver-version',help='Expected driver version for comparison')
    args=parser.parse_args()
    try:
        if args.image.stat().st_size>256*1024*1024:raise ValueError('Image exceeds 256 MiB bound')
        report=inspect_image(args.image.read_bytes(),args.driver_version)
    except (OSError,ValueError,struct.error) as error:parser.exit(1,str(error)+'\n')
    print(json.dumps(report,indent=2))
    if report['version_matches_driver'] is False:parser.exit(2,'Firmware/driver version mismatch\n')

if __name__=='__main__':main()

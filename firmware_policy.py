#!/usr/bin/env python3
"""Extract named PCIe reset policy candidates from unpacked UEFI firmware."""
import argparse,hashlib,json,os,pathlib,re,struct,subprocess,tempfile

PCD_GUID=bytes.fromhex('581ce806d74abc448390f10265f72480')
RESET_NAME=re.compile(r'^Pcd(?:Peg|Pcie).*(?:RstGpioNo|RstGpioPolarity|HoldRstActive|RootPort|GpioSupport)$')
PUSH=re.compile(rb'(?P<value>\x68....|\x6a.|[\x50-\x57])(?P<token>\x68....|\x6a.)\xff\x50(?P<slot>[\x3c\x40\x44\x50])',re.S)


def names_from_metadata(data):
    """Observed AMI metadata: 3 UINT64 fields, then token/name/namespace records.

    This is a bounded parser for the observed layout, not the standard EDK2
    runtime PCD database. Unsupported layouts return no names.
    """
    if len(data)<24:return {}
    name_size,space_size,count=struct.unpack_from('<QQQ',data)
    if not (8<=name_size<=256 and 8<=space_size<=256 and 0<count<=65535):return {}
    stride=2+name_size+space_size
    if 24+count*stride>len(data):return {}
    result={}
    for i in range(count):
        start=24+i*stride;token=struct.unpack_from('<H',data,start)[0]
        name=data[start+2:start+2+name_size].split(b'\0',1)[0]
        space=data[start+2+name_size:start+stride].split(b'\0',1)[0]
        if not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*',name) or not re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*',space):return {}
        if token in result:return {}
        result[token]={'name':name.decode(),'namespace':space.decode()}
    return result


def code_ranges(data):
    try:
        if data[:2]==b'VZ':
            machine=struct.unpack_from('<H',data,2)[0];count=data[4]
            delta=struct.unpack_from('<H',data,6)[0]-40;table=40
        elif data[:2]==b'MZ':
            pe=struct.unpack_from('<I',data,60)[0]
            if data[pe:pe+4]!=b'PE\0\0':return []
            machine,count=struct.unpack_from('<HH',data,pe+4)
            optional_size=struct.unpack_from('<H',data,pe+20)[0];table=pe+24+optional_size;delta=0
        else:return []
        if machine!=0x14c or not 0<count<=96:return [] # IA32 writer pattern only.
        result=[]
        for i in range(count):
            start=table+40*i
            size,pointer=struct.unpack_from('<II',data,start+16)
            flags=struct.unpack_from('<I',data,start+36)[0];offset=pointer-delta
            if flags&0x20 and 0<=offset<len(data) and offset+size<=len(data):result.append((offset,offset+size))
        return result
    except (struct.error,IndexError):return []


def immediate(push):
    if push[0]==0x68:return int.from_bytes(push[1:],'little')
    if push[0]==0x6a:return int.from_bytes(push[1:],'little',signed=True)&0xffffffff
    return None


def policy_writes(data,names):
    # The GUID check is corroboration, not full control-flow proof of every call.
    if PCD_GUID not in data:return []
    result=[]
    for start,end in code_ranges(data):
        for match in PUSH.finditer(data,start,end):
            token=immediate(match['token']);entry=names.get(token)
            if not entry or not RESET_NAME.fullmatch(entry['name']):continue
            slot=match['slot'][0]
            result.append(dict(entry,token=token,file_offset=match.start(),
                operation={0x3c:'Set8',0x40:'Set16',0x44:'Set32',0x50:'SetBool'}[slot],
                value=immediate(match['value']),
                value_register=None if immediate(match['value']) is not None else match['value'][0]-0x50,
                bytes=match[0].hex(),status='static call-site candidate; branch and caller validation required'))
    return result


def module_name(path):
    for parent in path.parents:
        info=parent/'info.txt'
        if info.exists():
            source=info.read_text(errors='replace')
            if source.startswith('Type: File'):
                match=re.search(r'^Text: (.+)$',source,re.M)
                if match:return match[1]
    return path.name


def scan(directory):
    names={};databases=[];modules=[];seen=set()
    for path in sorted(pathlib.Path(directory).rglob('body.bin')):
        if path.stat().st_size>16*1024*1024:continue
        data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        decoded=names_from_metadata(data)
        if decoded:databases.append((digest,decoded))
        if code_ranges(data) and PCD_GUID in data:modules.append((module_name(path),digest,data))
    # Token numbers are local to firmware builds. Keep databases separate; do
    # not merge incompatible namespaces or silently resolve conflicting names.
    reports=[]
    for digest,names in databases:
        writes=[]
        for name,module_hash,data in modules:
            for item in policy_writes(data,names):writes.append(dict(item,module=name,module_sha256=module_hash))
        relevant={str(token):entry for token,entry in names.items() if RESET_NAME.fullmatch(entry['name'])}
        summaries=[]
        for name in sorted({item['name'] for item in writes}):
            sites=[item for item in writes if item['name']==name]
            values=sorted({item['value'] for item in sites if item['value'] is not None})
            unknown=sum(item['value'] is None for item in sites)
            summaries.append({'name':name,'observed_call_sites':len(sites),
                'constant_values':[hex(value) for value in values],'unresolved_register_sites':unknown,
                'observed_writes_agree':len(values)==1 and unknown==0})
        reports.append({'metadata_sha256':digest,'total_named_tokens':len(names),
                        'reset_policy_tokens':relevant,'assignment_summary':summaries,'assignments':writes})
    return {'format':'AMI named PCD metadata + IA32 PcdPpi writer patterns',
            'databases':reports,'status':'discovered policy candidates; no hardware actions performed',
            'limits':['IA32 immediate/register push patterns only; x64 writers are not decoded',
                      'Stored assignments may belong to inactive board/SKU branches',
                      'Register arguments remain unknown; polarity is not inferred',
                      'Firmware policy is not proof of current wiring or reset scope']}


def live_scan(extractor):
    from gpu_recovery import trusted_file
    if os.geteuid():raise RuntimeError('--live requires root')
    extractor=str(trusted_file(extractor));matches=[]
    for path in pathlib.Path('/sys/class/mtd').glob('mtd[0-9]*'):
        if path.name.endswith('ro'):continue
        if (path/'name').read_text().strip().lower()=='bios':matches.append(path)
    if len(matches)!=1:raise RuntimeError('Exactly one BIOS MTD device is required')
    size=int((matches[0]/'size').read_text())
    if not 0<size<=128*1024*1024:raise RuntimeError('Unsupported BIOS MTD size')
    source=pathlib.Path('/dev')/(matches[0].name+'ro')
    with tempfile.TemporaryDirectory(prefix='gpu-policy-') as temporary:
        image=pathlib.Path(temporary)/'bios.bin'
        with source.open('rb',buffering=0) as reader,image.open('wb') as writer:
            remaining=size
            while remaining:
                chunk=reader.read(min(1024*1024,remaining))
                if not chunk:raise RuntimeError('Short BIOS MTD read')
                writer.write(chunk);remaining-=len(chunk)
        result=subprocess.run([extractor,str(image),'all'],stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE,timeout=90)
        if result.returncode:raise RuntimeError('UEFIExtract failed: '+result.stderr.decode(errors='replace')[-1000:])
        report=scan(str(image)+'.dump');report['bios_sha256']=hashlib.sha256(image.read_bytes()).hexdigest()
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',nargs='?',help='UEFIExtract output directory')
    parser.add_argument('--live',action='store_true',help='Read BIOS through /dev/mtdNro and extract in a private temporary directory')
    parser.add_argument('--runtime-acpi',action='store_true',help='Read reset fields from live ACPI NVS and correlate Linux GPIO ranges')
    parser.add_argument('--extractor',default='/usr/local/lib/gpu-recovery-generic/uefiextract')
    args=parser.parse_args()
    if args.live and args.directory:parser.error('Choose --live or an extracted directory')
    if not args.live and not args.directory and not args.runtime_acpi:parser.error('Specify --live, --runtime-acpi, or an extracted directory')
    report=live_scan(args.extractor) if args.live else scan(args.directory) if args.directory else {}
    if args.runtime_acpi:
        from firmware_runtime import live_nvs
        report['runtime_acpi']=live_nvs(pathlib.Path(__file__).resolve().parent/'references')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()

"""Read selected firmware NVS reset fields and correlate chipset pad metadata."""
import fcntl,hashlib,json,os,pathlib,re,shutil,struct,subprocess,tempfile


def field_layout(body):
    body=re.sub(r'/\*.*?\*/|//[^\n]*','',body,flags=re.S)
    parts=re.findall(r'[A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|\d+|[,()]|\S',body)
    fields={};offset=0;i=0
    def number(value):return {'Zero':0,'One':1}.get(value,int(value,0) if re.fullmatch(r'0x[0-9A-Fa-f]+|\d+',value) else -1)
    while i<len(parts):
        if parts[i]==',':
            if i+1<len(parts) and re.fullmatch(r'\d+',parts[i+1]):offset+=int(parts[i+1]);i+=2
            else:i+=1
        elif parts[i]=='Offset' and parts[i+1:i+2]==['('] and i+3<len(parts) and parts[i+3]==')':
            value=number(parts[i+2])
            if value<0 or value*8<offset:raise ValueError('Unsupported/reversed field offset')
            offset=value*8;i+=4
        elif re.fullmatch(r'[A-Z_][A-Z0-9_]{3}',parts[i]) and i+2<len(parts) and parts[i+1]==',' and parts[i+2].isdigit():
            name=parts[i];width=int(parts[i+2])
            if name in fields or not 0<width<=64:raise ValueError('Unsupported ACPI field')
            fields[name]=(offset,width);offset+=width;i+=3
        else:raise ValueError('Unsupported ACPI field syntax: '+parts[i])
    return fields,offset


def nvs_definition(source):
    clean=re.sub(r'/\*.*?\*/|//[^\n]*','',source,flags=re.S)
    regions=list(re.finditer(r'OperationRegion\s*\(GNVS,\s*SystemMemory,\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+)\)',clean))
    bodies=list(re.finditer(r'Field\s*\(GNVS,[^)]*\)\s*\{([^{}]*)\}',clean,re.S))
    if len(regions)!=1:raise ValueError('A unique literal GNVS region is required')
    base,size=(int(v,16) for v in regions[0].groups())
    if not base or not 0<size<=65536:raise ValueError('Unsupported NVS range')
    complete=[]
    for body in bodies:
        try:fields,bits=field_layout(body[1])
        except ValueError:continue
        if bits==size*8:complete.append(fields)
    if len(complete)!=1:raise ValueError('A unique complete NVS field layout is required')
    return base,size,complete[0]


def nvs_resource(address,size,iomem):
    for line in iomem.splitlines():
        match=re.fullmatch(r'\s*([0-9a-fA-F]+)-([0-9a-fA-F]+) : (.*)',line)
        if match and int(match[1],16)<=address and int(match[2],16)>=address+size-1 and match[3]=='ACPI Non-volatile Storage':return True
    return False


def gpio_mapping(pad,reference,controller,ranges):
    if controller.split(':')[0] not in reference['acpi_hids'] or pad&0xff000000!=reference['pad_prefix']:return None
    group=reference['groups'].get(str((pad>>16)&255));index=pad&65535
    if not group or index>=group['count']:return None
    pin=group['physical_start']+index
    for line in ranges.splitlines():
        match=re.fullmatch(r'\s*(\d+):\s+(\S+)\s+GPIOS \[(\d+) - (\d+)\] PINS \[(\d+) - (\d+)\]',line)
        if not match or match[2]!=controller:continue
        relative,global_first,global_last,first,last=map(int,(match[1],match[3],match[4],match[5],match[6]))
        if first!=group['physical_start'] or last-first+1!=group['count'] or global_last-global_first!=last-first:continue
        return {'controller':controller,'pad':group['name']+str(index),'physical_pin':pin,
                'gpio_line_offset':relative+pin-first,'reference_family':reference['family']}
    return None


def live_nvs(reference_dir):
    from gpu_recovery import trusted_file,gpio_inventory
    if os.geteuid():raise RuntimeError('Runtime NVS discovery requires root')
    executable=shutil.which('iasl')
    if not executable:raise RuntimeError('ACPICA iasl is required')
    executable=str(trusted_file(executable));table=pathlib.Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    with tempfile.TemporaryDirectory(prefix='gpu-nvs-') as directory:
        path=pathlib.Path(directory)/'dsdt.aml';path.write_bytes(table)
        result=subprocess.run([executable,'-d',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60)
        output=path.with_suffix('.dsl')
        if result.returncode or not output.exists():raise RuntimeError('DSDT disassembly failed; no physical memory read attempted')
        address,size,fields=nvs_definition(output.read_text(errors='replace'))
    if not nvs_resource(address,size,pathlib.Path('/proc/iomem').read_text()):raise RuntimeError('GNVS is not inside an ACPI Non-volatile Storage resource')
    descriptor=os.open('/dev/mem',os.O_RDONLY|os.O_CLOEXEC)
    try:data=os.pread(descriptor,size,address)
    finally:os.close(descriptor)
    if len(data)!=size:raise RuntimeError('Short NVS read')
    integer=int.from_bytes(data,'little');selected={}
    for name,(bit,width) in fields.items():
        if re.fullmatch(r'P[12](?:PE|PP|RE|RP|WP)|PRP[12]',name):
            selected[name]={'value':(integer>>bit)&((1<<width)-1),'bit_offset':bit,'width':width}
    mappings=[];references=[]
    for path in sorted(pathlib.Path(reference_dir).glob('*.json')):
        references.append(json.loads(path.read_text()))
    for slot in (1,2):
        reset=selected.get(f'P{slot}RE');polarity=selected.get(f'P{slot}RP')
        if not reset or not reset['value']:continue
        for chip in gpio_inventory():
            if not chip.get('label'):continue
            ranges_path=pathlib.Path('/sys/kernel/debug/pinctrl')/chip['label']/'gpio-ranges'
            try:ranges=ranges_path.read_text()
            except OSError:continue
            for reference in references:
                mapped=gpio_mapping(reset['value'],reference,chip['label'],ranges)
                if mapped:
                    mappings.append(dict(mapped,firmware_slot=slot,gpio_device='/dev/'+chip['controller'],
                        reset_pad_encoded=hex(reset['value']),polarity_value=polarity['value'] if polarity else None,
                        port_value=selected.get(f'PRP{slot}',{}).get('value')))
    return {'source':'Live GNVS, DSDT layout, chipset metadata and kernel GPIO ranges',
            'dsdt_sha256':hashlib.sha256(table).hexdigest(),'nvs_address':hex(address),'nvs_size':size,
            'fields':selected,'gpio_mappings':mappings,
            'status':'Read-only mapping; GPIO polarity semantics, GPU-slot association and reset scope require validation'}

#!/usr/bin/env python3
"""Discover and recover NVIDIA GPUs using host-exposed reset capabilities."""
import argparse, collections, csv, dataclasses, fcntl, hashlib, json, os, pathlib, pwd, re, signal, subprocess, sys, time
import xml.etree.ElementTree as ET
import shutil, tempfile, queue, threading

BDF = re.compile(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]')
SYSFS = pathlib.Path('/sys/bus/pci/devices')
ORPHANS = []
PROFILE_DIR = pathlib.Path('/etc/gpu-recovery/profiles.d')
DMI = pathlib.Path('/sys/class/dmi/id')
SERVICE_STATE = pathlib.Path('/var/lib/gpu-recovery-generic/services.json')

class RecoveryError(RuntimeError): pass
class CommandTimeout(RecoveryError): pass
class IdentityMismatch(RecoveryError): pass

@dataclasses.dataclass
class Target:
    bdf: str
    slot: list
    bridge: str | None
    isolated: bool
    reset_methods: list
    drivers: dict

def text(path, default=''):
    try: return pathlib.Path(path).read_text().strip()
    except OSError: return default

def discover(root=SYSFS):
    result=[]
    for p in sorted(root.iterdir()):
        klass=text(p/'class')
        if BDF.fullmatch(p.name) and text(p/'vendor')=='0x10de' and klass.startswith(('0x03','0x1200')):
            result.append(p.name)
    return result

def inspect(bdf,root=SYSFS):
    if not BDF.fullmatch(bdf) or bdf not in discover(root): raise RecoveryError('Select a discovered NVIDIA GPU or processing accelerator')
    path=(root/bdf).resolve(); parent=path.parent
    slot=sorted(p.name for p in root.iterdir() if BDF.fullmatch(p.name) and p.name[:-1]==bdf[:-1])
    bridge=parent.name if BDF.fullmatch(parent.name) else None
    # Include descendants: a bridge or another endpoint in scope is a refusal.
    descendants={p.name for p in parent.rglob('*') if BDF.fullmatch(p.name)} if bridge else set()
    isolated=bool(bridge) and descendants==set(slot)
    drivers={p: (root/p/'driver').resolve().name for p in slot if (root/p/'driver').is_symlink()}
    return Target(bdf,slot,bridge,isolated,text(root/bdf/'reset_method').split(),drivers)

def trusted_file(path):
    p=pathlib.Path(path)
    if not p.is_absolute(): raise RecoveryError('Privileged paths must be absolute')
    try: p=p.resolve(strict=True)
    except OSError as e: raise RecoveryError(f'Privileged path unavailable: {e}') from e
    for part in [p,*p.parents]:
        st=part.stat()
        if st.st_uid!=0 or st.st_mode & 0o022: raise RecoveryError(f'Path is not exclusively root-managed: {part}')
    return p

def config(path,privileged=False):
    if not path: return {}
    if privileged: trusted_file(path)
    value=json.loads(pathlib.Path(path).read_text())
    if not isinstance(value,dict): raise RecoveryError('Configuration must be a JSON object')
    return value

def run(argv,timeout=20,env=None):
    if not isinstance(argv,list) or not argv or not all(isinstance(a,str) for a in argv):
        raise RecoveryError('Commands must be nonempty argument arrays')
    p=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                       start_new_session=True,env=env)
    try: out,_=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try: os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        if p.stdout: p.stdout.close()
        # Reap a normal terminated process, but never wait indefinitely for D-state.
        try: p.wait(timeout=.1)
        except subprocess.TimeoutExpired: ORPHANS.append(p)
        raise CommandTimeout(f'{argv[0]} timed out; blocked PID {p.pid} may remain. No further reset will be attempted.')
    return p.returncode,out.strip()

def write_sysfs(path,value):
    # Separate process so a blocking sysfs write cannot trap this controller.
    rc,out=run([sys.executable,'-c',
                'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])',str(path),value],15)
    if rc: raise RecoveryError(f'Sysfs operation failed: {out}')

def log(message): print(time.strftime('%Y-%m-%d %H:%M:%S ')+message,flush=True)

def backend_for(target,cfg):
    return cfg.get('backends',{}).get(target.bdf)

def pci_capabilities(data):
    """Decode advertised capabilities without changing PCI registers."""
    result={'standard':[],'extended':[]}
    def number(offset,size):
        return int.from_bytes(data[offset:offset+size],'little') if offset+size<=len(data) else None
    if len(data)<64 or data[:2] in (b'\xff\xff',b'\x00\x00'): return result
    header=data[14]&0x7f
    pointer=(number(0x34 if header in (0,1) else 0x14,1) or 0)&0xfc
    seen=set()
    while pointer>=0x40 and pointer+2<=min(len(data),256) and pointer not in seen:
        seen.add(pointer);cap=data[pointer];item={'id':cap,'offset':pointer}
        if cap==1:
            item['name']='Power management';pm=number(pointer+4,2)
            item['power_state']=pm&3 if pm is not None else None
            item['no_soft_reset']=bool(pm&8) if pm is not None else None
        elif cap==0x10:
            item['name']='PCI Express';flags=number(pointer+2,2);devcap=number(pointer+4,4)
            item['port_type']=(flags>>4)&15 if flags is not None else None
            item['flr_advertised']=bool(devcap&(1<<28)) if devcap is not None else None
            link=number(pointer+0x12,2)
            item['link_status']=hex(link) if link is not None else None
            control=number(pointer+0x10,2)
            item['link_disabled']=bool(control&0x10) if control is not None else None
            item['link_training']=bool(link&0x800) if link is not None else None
            item['data_link_active']=bool(link&0x2000) if link is not None else None
            linkcap=number(pointer+0xc,4)
            item['data_link_active_reporting']=bool(linkcap&(1<<20)) if linkcap is not None else None
            item['link_bandwidth_management_status']=bool(link&0x4000) if link is not None else None
            item['current_link_speed_code']=link&15 if link is not None else None
            item['current_link_width']=(link>>4)&63 if link is not None else None
            version=flags&15 if flags is not None else None
            control2=number(pointer+0x30,2) if version is not None and version>=2 else None
            item['target_link_speed_code']=control2&15 if control2 is not None else None
            slot=number(pointer+0x14,4)
            item['slot_power_controller']=bool(slot&2) if flags is not None and flags&0x100 and item['port_type'] in (4,6) and slot is not None else None
        elif cap in (5,0x11): item['name']='MSI' if cap==5 else 'MSI-X'
        result['standard'].append(item);pointer=data[pointer+1]&0xfc
    pointer=0x100;seen=set()
    names={1:'AER',0xb:'Vendor specific',0xd:'ACS',0x10:'SR-IOV',0x15:'Resizable BAR',0x1d:'DPC'}
    while pointer>=0x100 and pointer%4==0 and pointer+4<=len(data) and pointer not in seen:
        seen.add(pointer);value=number(pointer,4)
        if value in (0,0xffffffff): break
        cap=value&0xffff
        item={'id':cap,'name':names.get(cap,'Other'),'version':(value>>16)&15,'offset':pointer}
        if cap==1:
            for field,offset in (('uncorrectable_status',4),('uncorrectable_mask',8),
                                 ('fatal_severity',12),('correctable_status',16),('correctable_mask',20)):
                register=number(pointer+offset,4)
                item[field]=hex(register) if register is not None else None
        elif cap==0x1d:
            control=number(pointer+6,2);status=number(pointer+8,2)
            item['control']=hex(control) if control is not None else None
            item['status']=hex(status) if status is not None else None
            item['triggered']=bool(status&1) if status is not None else None
            item['trigger_reason']=(status>>1)&3 if status is not None else None
            item['root_port_busy']=bool(status&0x10) if status is not None else None
        result['extended'].append(item)
        pointer=(value>>20)&0xfff
    return result

def config_bytes(path):
    try:
        with pathlib.Path(path).open('rb',buffering=0) as handle:
            data=handle.read(256)
            try: data+=handle.read(3840)
            except OSError: pass  # Some platforms expose only legacy config space.
            return data
    except OSError: return b''

def gpio_inventory():
    chips=[]
    for device in sorted(pathlib.Path('/dev').glob('gpiochip*')):
        item={'controller':device.name,'label':None,'lines':None}
        try:
            fd=os.open(device,os.O_RDONLY|os.O_CLOEXEC)
            try:
                # Linux GPIO_GET_CHIPINFO_IOCTL: metadata only, no line request.
                info=bytearray(68);fcntl.ioctl(fd,0x8044b401,info,True)
                item['label']=bytes(info[32:64]).split(b'\0',1)[0].decode('utf-8','replace')
                item['lines']=int.from_bytes(info[64:68],sys.byteorder)
                item['named_lines']=[]
                for offset in range(item['lines']):
                    line=bytearray(72);line[:4]=offset.to_bytes(4,sys.byteorder)
                    # GPIO_GET_LINEINFO_IOCTL reads names, ownership and flags only.
                    try: fcntl.ioctl(fd,0xc048b402,line,True)
                    except OSError as e:
                        item['line_info_error']=str(e);break
                    name=bytes(line[8:40]).split(b'\0',1)[0].decode('utf-8','replace')
                    consumer=bytes(line[40:72]).split(b'\0',1)[0].decode('utf-8','replace')
                    if name or consumer:
                        flags=int.from_bytes(line[4:8],sys.byteorder)
                        item['named_lines'].append({'offset':offset,'name':name,'consumer':consumer,
                            'kernel_owned':bool(flags&1),'output':bool(flags&2),'active_low':bool(flags&4),
                            'reset_name_candidate':bool(re.search(r'perst|pcie.*rst|gpu.*rst|reset',name+' '+consumer,re.I))})
            finally: os.close(fd)
        except OSError as e: item['unavailable']=str(e)
        chips.append(item)
    return chips

def acpi_path(value):
    return tuple(part.rstrip('_') for part in value.strip().lstrip('\\').split('.') if part)

def asl_reset_objects(source,include_helpers=False):
    """Static namespace extraction, not an AML interpreter or wiring proof."""
    source=re.sub(r'/\*.*?\*/|//[^\n]*',lambda m:' '*len(m[0]),source,flags=re.S)
    tokens=list(re.finditer(r'"(?:\\.|[^"\\])*"|[A-Za-z_\\^][A-Za-z0-9_\\^.]*|[{}(),]|[^\s]',source))
    pairs={};stack=[]
    for i,token in enumerate(tokens):
        value=token[0]
        if value in ('(','{'): stack.append((value,i))
        elif value in (')','}') and stack:
            kind,start=stack.pop()
            if (kind,value) in (('(',')'),('{','}')): pairs[start]=i
    result=[]
    def resolve(name,parent):
        if name.startswith('\\'): return acpi_path(name)
        while name.startswith('^'):
            parent=parent[:-1];name=name[1:]
        return parent+acpi_path(name)
    def scan(start,end,parent):
        i=start
        while i<end:
            kind=tokens[i][0]
            if kind in ('Scope','Device','PowerResource','Method','Name') and i+2<end and tokens[i+1][0]=='(':
                close=pairs.get(i+1)
                if close is None: i+=1;continue
                name=tokens[i+2][0];path=resolve(name,parent)
                body=close+1;body_end=pairs.get(body) if body<end and tokens[body][0]=='{' else None
                finish=body_end if body_end is not None else close
                snippet=source[tokens[i].start():tokens[finish].end()]
                relevant=(kind=='Method' and name.split('.')[-1] in ('_RST','_PR0','_PR3','_PRR','_PS0','_PS3','_ON','_OFF') or kind=='Name' and (name.split('.')[-1] in ('_PR0','_PR3','_PRR') or name.split('.')[-1]=='_DSD' and re.search(r'"(?:reset-gpios|reset-gpio|gpio-line-names|FundamentalDeviceResetTriggeredOnD3ToD0)"',snippet)))
                if name.split('.')[-1]=='_DSD' and re.search(r'"(?:reset-gpios|reset-gpio|gpio-line-names|FundamentalDeviceResetTriggeredOnD3ToD0)"',snippet): relevant=True
                if relevant or include_helpers and kind in ('Method','PowerResource'):
                    result.append({'path':'\\'+'.'.join(path),'kind':kind,'source':snippet[:12000],
                                   'truncated':len(snippet)>12000,'reset_related':bool(relevant)})
                if body_end is not None:
                    scan(body+1,body_end,path if kind in ('Scope','Device','PowerResource') else parent)
                i=finish+1
            else: i+=1
    scan(0,len(tokens),())
    return result

def trace_acpi_helpers(objects,scope,limit=128):
    index={acpi_path(item['path']):item for item in objects}
    pending=[key for key in index if key[:-1] in {acpi_path(p) for p in scope} and index[key]['reset_related']]
    seen=set();result=[]
    while pending and len(seen)<limit:
        key=pending.pop(0)
        if key in seen: continue
        seen.add(key);item=dict(index[key]);calls=[];unresolved=[]
        # Only resolve literal calls to methods present in the decoded namespace.
        # AML indirection and runtime-created objects remain unresolved.
        for name in re.findall(r'([\\^A-Za-z_][\\^.A-Za-z0-9_]*)\s*\(',item['source']):
            base=key[:-1]
            if name.startswith('\\'): candidates=[acpi_path(name)]
            elif name.startswith('^'):
                while name.startswith('^'):base=base[:-1];name=name[1:]
                candidates=[base+acpi_path(name)]
            else: candidates=[base[:n]+acpi_path(name) for n in range(len(base),-1,-1)]
            found=next((path for path in candidates if path in index and index[path]['kind']=='Method'),None)
            if found is not None and found!=key:
                calls.append(index[found]['path']);pending.append(found)
            elif found is None and re.fullmatch(r'[\\^A-Z0-9_.]+',name): unresolved.append(name)
        item['resolved_method_calls']=sorted(set(calls))
        item['unresolved_method_calls']=sorted(set(unresolved));result.append(item)
    return {'methods':result,'limit_reached':bool(pending),
            'coverage':'Literal method calls only; dynamic AML and external objects may remain unresolved'}

def acpi_resource_graph(objects,scope):
    """Find literal power/reset resource references; never execute AML."""
    index=collections.defaultdict(list)
    for item in objects:index[acpi_path(item['path'])].append(item)
    resources={key:items for key,items in index.items() if any(item['kind']=='PowerResource' for item in items)}
    references=collections.defaultdict(list);targets={acpi_path(path) for path in scope}
    unresolved=[]
    for key,items in index.items():
        if key[-1] not in ('_PR0','_PR3','_PRR'):continue
        for item in items:
            source=re.sub(r'"(?:\\.|[^"\\])*"','""',item['source'])
            for name in set(re.findall(r'[\\^A-Z_][\\^A-Z0-9_.]*',source)):
                base=key[:-1]
                if name.startswith('\\'):candidates=[acpi_path(name)]
                elif name.startswith('^'):
                    while name.startswith('^'):base=base[:-1];name=name[1:]
                    candidates=[base+acpi_path(name)]
                else:candidates=[base[:n]+acpi_path(name) for n in range(len(base),-1,-1)]
                found=next((path for path in candidates if path in index),None)
                if found in resources:
                    reference={'object':item['path'],'consumer':'\\'+'.'.join(key[:-1]),
                               'relationship':key[-1],
                               'target_scope':key[:-1] in targets}
                    if reference not in references[found]:references[found].append(reference)
            if re.search(r'\b(?:If|Else|While|DerefOf|Index)\s*\(',source):unresolved.append(item['path'])
    result=[]
    for key,consumers in references.items():
        if not any(item['target_scope'] for item in consumers):continue
        definitions=resources[key]
        ambiguous=len({item['source'] for item in definitions})>1
        path='\\'+'.'.join(key)
        methods=[item['path'] for item in objects if acpi_path(item['path'])[:-1]==key and item['kind']=='Method']
        result.append({'path':path,'references':consumers,
                       'other_static_consumers':sorted({item['consumer'] for item in consumers if not item['target_scope']}),
                       'sharing_status':'multiple_static_consumers' if len({item['consumer'] for item in consumers})>1 else 'no_additional_static_consumers_found',
                       'ambiguous_definition':ambiguous,'methods':sorted(set(methods)),
                       'reset_method_present':any(acpi_path(method)[-1]=='_RST' for method in methods)})
    resource_scopes=[item['path'] for item in result if not item['ambiguous_definition']]
    return {'target_resources':sorted(result,key=lambda item:item['path']),
            'resource_method_trace':trace_acpi_helpers(objects,resource_scopes),
            'dynamic_relationships':sorted(set(unresolved)),
            'coverage':'Literal references only; conditional, aliased, external or runtime-created relationships may be incomplete',
            'action':'Read-only; resource presence is not authorization to reset or power-cycle it'}

def firmware_reset_discovery(target,root=SYSFS):
    scope=[]
    for bdf in (target.bdf,target.bridge):
        if bdf:
            value=text(root/bdf/'firmware_node/path')
            if value: scope.append(value)
    result={'gpu_and_bridge_acpi_paths':scope,'status':'unavailable','objects':[],
            'action':'inspection only; firmware methods are not executed'}
    executable=shutil.which('iasl')
    if not executable:
        result['reason']='Install ACPICA iasl for static ACPI table decoding';return result
    if os.geteuid(): result['reason']='Root is needed to read ACPI tables';return result
    trusted_file(executable)
    with tempfile.TemporaryDirectory(prefix='gpu-acpi-') as directory:
        base=pathlib.Path(directory);tables=[]
        for path in sorted(pathlib.Path('/sys/firmware/acpi/tables').rglob('*')):
            if path.name=='DSDT' or path.name.startswith('SSDT'):
                try: blob=path.read_bytes()
                except OSError: continue
                if len(blob)>16*1024*1024: raise RecoveryError('ACPI table exceeds discovery limit')
                output=base/(('dynamic-' if path.parent.name=='dynamic' else '')+path.name+'.aml')
                output.write_bytes(blob);tables.append(str(output))
        if not tables: result['reason']='No readable DSDT/SSDT tables';return result
        rc,out=run([executable,'-da',*tables],60)
        fallback=[]
        if rc:
            # Some firmware defines duplicate objects across tables. Decode
            # independently as a fallback; external-call resolution is partial.
            deadline=time.monotonic()+60
            for table in tables:
                if time.monotonic()>=deadline: break
                code,diagnostic=run([executable,'-d',table],min(10,max(1,deadline-time.monotonic())))
                fallback.append({'table':pathlib.Path(table).stem,'returncode':code})
        objects=[];decoded=list(base.glob('*.dsl'))
        for path in decoded:
            for item in asl_reset_objects(path.read_text(errors='replace'),include_helpers=True):
                item['table']=path.stem
                object_path=acpi_path(item['path'])
                item['target_scope']=any(object_path[:-1]==acpi_path(p) for p in scope)
                objects.append(item)
        result.update(status='decoded' if rc==0 else 'partial decode',tables_decoded=len(decoded),
                      objects=[o for o in objects if o['reset_related']],
                      matching_scope_objects=[o['path'] for o in objects if o['target_scope'] and o['reset_related']],
                      target_method_trace=trace_acpi_helpers(objects,scope),
                      resource_graph=acpi_resource_graph(objects,scope),
                      decoder_returncode=rc,independent_table_fallback=fallback,
                      independent_perst_mapping='not established; declarations need resource and reset-scope verification')
        if rc: result['decoder_diagnostic']=out[-2000:]
    return result

def nvidia_inventory(target):
    """Driver-reported enterprise capabilities; absence is explicitly unknown."""
    result={'query_available':False,'mig_mode':None,'virtualization_mode':None,
            'fabric_state':None,'topology':None,'nvlink_present':None}
    try:
        rc,out=run(['/usr/bin/nvidia-smi','-i',target.bdf,'-q','-x'],15)
        if not rc:
            gpu=ET.fromstring(out).find('gpu')
            if gpu is not None:
                result.update(query_available=True,name=gpu.findtext('product_name'),
                    architecture=gpu.findtext('product_architecture'),uuid=gpu.findtext('uuid'),
                    recovery_action=gpu.findtext('gpu_recovery_action'),
                    gsp_firmware=gpu.findtext('gsp_firmware_version'),vbios=gpu.findtext('vbios_version'),
                    mig_mode=gpu.findtext('mig_mode/current_mig'),
                    virtualization_mode=gpu.findtext('gpu_virtualization_mode/virtualization_mode'),
                    fabric_state=gpu.findtext('gpu_fabric_info/state'))
        rc,out=run(['/usr/bin/nvidia-smi','topo','-m'],15)
        if not rc:
            result['topology']=out
            result['nvlink_present']=bool(re.search(r'\bNV\d+\b',out))
    except CommandTimeout: raise
    except (OSError,ET.ParseError,RecoveryError) as e: result['error']=str(e)
    return result

def hotplug_inventory(target,root=SYSFS,slots_root=pathlib.Path('/sys/bus/pci/slots')):
    """Read slot mappings and scope only; power files can remove whole subtrees."""
    device=(root/target.bdf).resolve()
    ancestors={p.name for p in [device,*device.parents] if BDF.fullmatch(p.name)}
    devices={p.name:p.resolve() for p in root.iterdir() if BDF.fullmatch(p.name)}
    result=[]
    for slot in sorted(slots_root.glob('*')):
        address=text(slot/'address')
        # A PCI slot address omits the function number; never match by slot label.
        if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}',address):continue
        members=[name for name in ancestors if name[:-2]==address]
        if not members:continue
        scope=set()
        for name,path in devices.items():
            if any(parent.name[:-2]==address and BDF.fullmatch(parent.name)
                   for parent in [path,*path.parents]):scope.add(name)
        result.append({'slot':slot.name,'address':address,'power_path':str(slot/'power') if (slot/'power').exists() else None,
                       'power':text(slot/'power') or None,'affected_bdfs':sorted(scope),
                       'target_functions_only':bool(scope) and scope==set(target.slot),
                       'full_gpu_power_removal_verified':False})
    return result

def pci_error_inventory(target,root=SYSFS):
    names=('aer_dev_correctable','aer_dev_nonfatal','aer_dev_fatal',
           'aer_rootport_total_err_cor','aer_rootport_total_err_nonfatal','aer_rootport_total_err_fatal')
    return {bdf:{name:text(root/bdf/name) for name in names if (root/bdf/name).is_file()}
            for bdf in (target.bdf,target.bridge) if bdf}

def subordinate_rescan_path(bridge,root=SYSFS):
    """Find the bridge's child-bus rescan; device/rescan scans its parent bus."""
    if not bridge or not BDF.fullmatch(bridge):return None
    buses=[p for p in (root/bridge/'pci_bus').glob('*')
           if re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}',p.name) and (p/'rescan').exists()]
    return buses[0]/'rescan' if len(buses)==1 else None

def switch_management_inventory(target,root=SYSFS,class_root=pathlib.Path('/sys/class/switchtec')):
    """List PCI ancestors and existing switch management functions; no commands."""
    device=(root/target.bdf).resolve()
    ancestors=[p for p in device.parents if BDF.fullmatch(p.name)]
    ancestor_names={p.name for p in ancestors}
    controllers=[]
    for entry in sorted(class_root.glob('*')):
        link=entry/'device'
        if not link.exists():continue
        path=link.resolve()
        controller_chain={p.name for p in [path,*path.parents] if BDF.fullmatch(p.name)}
        controllers.append({'device':entry.name,'pci_bdf':path.name if BDF.fullmatch(path.name) else None,
                            'partition':text(entry/'partition') or None,
                            'partition_count':text(entry/'partition_count') or None,
                            'firmware_version':text(entry/'fw_version') or None,
                            'depends_on_target_gpu':path==device or device in path.parents,
                            'shared_pci_ancestors':sorted(ancestor_names & controller_chain),
                            'gpu_port_mapping_verified':False,'transport_survival_verified':False})
    return {'pci_ancestors':[{'bdf':p.name,'vendor':text(p/'vendor'),'device':text(p/'device'),
                              'class':text(p/'class')} for p in ancestors],
            'switchtec_management_functions':controllers,
            'note':'Common PCI ancestry does not map a switch physical port or prove reset scope. Absent local management devices do not exclude external I2C/UART controllers.'}


def management_inventory(target):
    # Enumerate existing OS devices only; do not send SMBus or NSM messages.
    return {'i2c_adapters':[{'adapter':p.name,'name':text(p/'name'),
                            'depends_on_target_gpu':target.bdf in [parent.name for parent in p.resolve().parents]}
                           for p in sorted(pathlib.Path('/sys/bus/i2c/devices').glob('i2c-*'))],
            'nvidia_usb_candidates':[{'device':p.name,'vendor':text(p/'idVendor'),'product':text(p/'idProduct')}
                for p in sorted(pathlib.Path('/sys/bus/usb/devices').glob('*'))
                if text(p/'idVendor') in ('0955','10de')],
            'mctp_links':[p.name for p in pathlib.Path('/sys/class/net').glob('*') if text(p/'type')=='290'],
            'mapping_status':'Endpoint-to-GPU mapping and independent transport availability are not established by enumeration'}

def reset_assessment(report):
    reachable=report['config_space_reachable']
    caps=report['pci_capabilities']['standard']
    flr=next((c.get('flr_advertised') for c in caps if c['id']==0x10),None)
    return {'pci_config_reachable':reachable,'flr_advertised':flr,
            'nvml_recovery_action':report['nvidia'].get('recovery_action'),
            'in_band_reset_transport':'unreachable' if reachable is False else 'reachable' if reachable else 'unknown',
            'lost_link_route':'upstream PCI reset or independently mapped reset/power controller',
            'selected_lifecycle_backend':bool(report['selected_backend']),
            'note':'Advertised FLR does not establish recovery from a lost PCIe link'}

def kernel_fallback_allowed(report,gpu_count):
    nvidia=report.get('nvidia',{})
    if gpu_count>1:
        raise RecoveryError('Multi-GPU kernel fallback requires a verified lifecycle adapter; peer reset scope cannot be inferred from PCI isolation')
    if nvidia.get('nvlink_present'):
        raise RecoveryError('NVLink fabric detected; generic PCI fallback refused')
    if (nvidia.get('mig_mode') or '').lower()=='enabled':
        raise RecoveryError('MIG enabled; generic PCI fallback refused without an instance-aware adapter')
    virtualization=(nvidia.get('virtualization_mode') or '').lower()
    if virtualization and virtualization not in ('none','n/a','not supported'):
        raise RecoveryError('Virtualized GPU; generic PCI fallback requires a host-aware adapter')

def kernel_fallback_assessment(report,gpu_count):
    """Explain existing runtime guards, without authorizing a reset."""
    blockers=[]
    try:kernel_fallback_allowed(report,gpu_count)
    except RecoveryError as error:blockers.append(str(error))
    target=report['target'];kernel=report['kernel_reset']
    if not target['isolated']:blockers.append('Bridge contains other devices or isolation is unknown')
    if target['drivers'].get(target['bdf']) not in (None,'nvidia'):
        blockers.append('Target uses VFIO or another unsupported driver lifecycle')
    if kernel.get('selection_error'):blockers.append(kernel['selection_error'])
    elif not kernel.get('candidate_paths'):blockers.append('No kernel reset path selected')
    nvidia=report['nvidia']
    unknown=[field for field in ('nvlink_present','mig_mode','virtualization_mode') if nvidia.get(field) is None]
    return {'gpu_count':gpu_count,'blocked_by_existing_guards':bool(blockers),'blockers':blockers,
            'unknown_nvidia_fields':unknown,'hardware_recovery_verified':False,
            'note':'No listed blocker does not establish recovery support; active clients, command failures, timeouts and post-reset validation are checked during an attempt'}

def explore(target,cfg,root=SYSFS,profile_dir=PROFILE_DIR):
    """Inventory only: never evaluate ACPI methods or request/toggle GPIO lines."""
    board={key:text(DMI/key) for key in ('board_vendor','board_name','board_version',
            'product_name','bios_vendor','bios_version','bios_date')}
    device=root/target.bdf
    pci={key:text(device/key) for key in ('vendor','device','subsystem_vendor','subsystem_device',
            'current_link_speed','current_link_width','max_link_speed','max_link_width')}
    raw=config_bytes(device/'config')
    reachable=(raw[:2] not in (b'\xff\xff',b'\x00\x00')) if len(raw)>=2 else None
    gpio=gpio_inventory()
    rescan=subordinate_rescan_path(target.bridge,root)
    report={'board':board,'kernel':os.uname().release,'nvidia_version':text('/sys/module/nvidia/version'),
            'target':dataclasses.asdict(target),'pci':pci,'config_space_reachable':reachable,
            'kernel_reset':{'function':bool(target.reset_methods) and (device/'reset').exists(),
                            'subordinate':bool(target.bridge) and (root/target.bridge/'reset_subordinate').exists(),
                            'isolated':target.isolated},
            'firmware_node':str((device/'firmware_node').resolve()) if (device/'firmware_node').exists() else None,
            'gpio_controllers':gpio,'ipmi_devices':[str(p) for p in pathlib.Path('/dev').glob('ipmi*')],
            'pci_capabilities':pci_capabilities(raw),
            'pci_resources':text(device/'resource').splitlines(),
            'iommu_group':str((device/'iommu_group').resolve()) if (device/'iommu_group').exists() else None,
            'chipset_devices':[], 'acpi_tables':[],
            'nvidia':nvidia_inventory(target),
            'management':management_inventory(target),
            'switch_management':switch_management_inventory(target,root),
            'hotplug_slots':hotplug_inventory(target,root),
            'pci_error_counters':pci_error_inventory(target,root),
            'subordinate_rescan_path':str(rescan) if rescan else None,
            'upstream_capabilities':pci_capabilities(config_bytes(root/target.bridge/'config')) if target.bridge else None,
            'd3cold_allowed_policy':text(device/'d3cold_allowed'),
            'profiles':[],'selected_backend':backend_for(target,cfg),
            'unverified_candidates_policy':'Inventory only; GPIO wiring and firmware reset scope require a verified adapter'}
    report['kernel_reset']['mode']=cfg.get('kernel_reset_mode','auto')
    try:report['kernel_reset']['candidate_paths']=[str(path) for path in kernel_reset_paths(target,cfg,root)]
    except RecoveryError as error:report['kernel_reset']['selection_error']=str(error)
    for entry in sorted(root.iterdir()):
        klass=text(entry/'class')
        if klass.startswith(('0x06','0x0c05')):
            report['chipset_devices'].append(dict(bdf=entry.name,
                **{key:text(entry/key) for key in ('vendor','device','class','revision')},
                capabilities=pci_capabilities(config_bytes(entry/'config')) if entry.name==target.bridge else None))
    for table in sorted(pathlib.Path('/sys/firmware/acpi/tables').glob('*')):
        if not table.is_file(): continue
        try:
            blob=table.read_bytes()
            report['acpi_tables'].append({'signature':table.name,'bytes':len(blob),
                'oem_id':blob[10:16].decode('ascii','replace').strip(),
                'oem_table_id':blob[16:24].decode('ascii','replace').strip(),
                'sha256':hashlib.sha256(blob).hexdigest()})
        except OSError: report['acpi_tables'].append({'signature':table.name,'readable':False})
    matches=[]
    if cfg.get('deep_discovery'):
        report['firmware_reset_discovery']=firmware_reset_discovery(target,root)
    for path in sorted(pathlib.Path(profile_dir).glob('*.json')):
        item={'file':str(path),'status':'unavailable'}
        report['profiles'].append(item)
        try:
            profile=config(path,True)
            item['id']=profile.get('id',path.stem)
            constraints=profile.get('match',{})
            # Profiles are root-managed and must bind to both board and PCI identity.
            if not constraints.get('board_name') or not constraints.get('vendor') or not constraints.get('device'):
                raise RecoveryError('Profile requires board_name, vendor and device constraints')
            observed=dict(board,**pci,bdf=target.bdf)
            if any(key not in observed or observed[key]!=value for key,value in constraints.items()):
                item['status']='hardware mismatch';continue
            backend=profile.get('backend',{})
            command=backend.get('command')
            if not isinstance(command,list) or not command: raise RecoveryError('Profile has no backend command')
            trusted_file(command[0])
            if backend.get('requires_single_gpu') and len(discover(root))!=1:
                raise RecoveryError('Profile requires a single GPU')
            if not target.isolated: raise RecoveryError('Profile reset scope is shared or unknown')
            check=profile.get('check_command')
            if not isinstance(check,list) or not check: raise RecoveryError('Profile requires a read-only compatibility check')
            trusted_file(check[0])
            if os.geteuid():
                item['status']='hardware matched; root compatibility check required';continue
            rc,out=run(check,30)
            item['check_output']=out
            if rc: raise RecoveryError('Adapter compatibility check failed')
            backend=dict(backend,match={key:value for key,value in constraints.items()
                                      if key in ('board_name','board_vendor','product_name')})
            matches.append((item['id'],backend,profile.get('validation_command')))
            item['status']='verified compatible'
        except CommandTimeout: raise
        except (RecoveryError,OSError,ValueError,TypeError) as e: item['status']=str(e)
    if report['selected_backend']:
        report['selection']='explicit configured backend'
    elif len(matches)>1:
        raise RecoveryError('Multiple compatible profiles; select an explicit backend in configuration')
    elif matches:
        profile_id,backend,validation=matches[0]
        report['selected_backend']=backend
        report['validation_command']=validation
        report['selection']='verified profile: '+profile_id
    else:
        report['selection']='targeted NVML reset, then isolated kernel reset if available'
    report['reset_assessment']=reset_assessment(report)
    report['kernel_fallback_assessment']=kernel_fallback_assessment(report,len(discover(root)))
    return report

def discovered_config(report,cfg):
    effective=dict(cfg)
    if report['selected_backend']:
        effective['backends']=dict(cfg.get('backends',{}))
        effective['backends'][report['target']['bdf']]=report['selected_backend']
        if report.get('validation_command') and not effective.get('validation_command'):
            effective['validation_command']=report['validation_command']
    return effective

def plan(target,cfg):
    return dict(dataclasses.asdict(target),
                nvml_reset='target GPU only; refuses required peer expansion',
                sysfs_reset=target.isolated and bool(target.reset_methods),
                subordinate_reset=target.isolated and bool(target.bridge) and (SYSFS/target.bridge/'reset_subordinate').exists(),
                backend=backend_for(target,cfg),
                limits='No arbitrary GPIO/firmware writes, global module unload, reboot, or reset of other slots')

def service_items(cfg,bdf):
    items=cfg.get('services',[])+cfg.get('gpu_services',{}).get(bdf,[])
    result=[]
    for item in items:
        if not isinstance(item,dict) or set(item)-{'unit','user'} or 'unit' not in item:
            raise RecoveryError('Service entries require unit and optional user only')
        if item not in result:result.append(item)
    return result

class Services:
    def __init__(self,items,gpu=None,state_path=None):
        self.items=items;self.gpu=gpu;self.path=state_path or SERVICE_STATE;self.stopped=[]
        if items and (not isinstance(gpu,str) or not BDF.fullmatch(gpu)):
            raise RecoveryError('Service holds require a GPU PCI identity')
    def command(self,item,verb):
        unit=item['unit']
        if not re.fullmatch(r'[A-Za-z0-9_.@:\\-]+\.service',unit): raise RecoveryError('Invalid service unit')
        if 'user' not in item: return ['/usr/bin/systemctl',verb,unit]
        user=pwd.getpwnam(item['user']);uid=user.pw_uid
        return ['/usr/sbin/runuser','-u',user.pw_name,'--','/usr/bin/env',
                f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
                '/usr/bin/systemctl','--user',verb,unit]
    def _load(self):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        trusted_file(self.path.parent)
        if not self.path.exists():return {'version':1,'items':{}}
        trusted_file(self.path)
        value=json.loads(self.path.read_text())
        if value.get('version')!=1 or not isinstance(value.get('items'),dict):
            raise RecoveryError('Invalid service hold ledger')
        return value
    def _save(self,value):
        # Directory is exclusively root-managed before this atomic replacement.
        descriptor,name=tempfile.mkstemp(prefix='.services-',dir=self.path.parent)
        try:
            with os.fdopen(descriptor,'w') as stream:json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)
    def _lock(self):
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        trusted_file(self.path.parent)
        handle=(self.path.parent/'services.lock').open('a')
        fcntl.flock(handle,fcntl.LOCK_EX);return handle
    def stop(self):
        if not self.items:return
        with self._lock():
            state=self._load()
            for item in self.items:
                argv=self.command(item,'stop')  # Validate before recording.
                key=json.dumps(item,sort_keys=True)
                active,out=run(self.command(item,'is-active'))
                wanted=active==0 or out.strip() in ('activating','reloading')
                record=state['items'].get(key)
                if not record and not wanted:continue
                if not record:record={'service':item,'blocked_by':[]}
                if self.gpu not in record['blocked_by']:record['blocked_by'].append(self.gpu)
                state['items'][key]=record;self._save(state)
                self.stopped.append(item)
                rc,out=run(argv,90)
                if rc:raise RecoveryError(f'Could not drain {item}: {out}')
    def restore(self):
        if not self.items:return
        errors=[]
        with self._lock():
            state=self._load()
            for item in reversed(self.items):
                key=json.dumps(item,sort_keys=True);record=state['items'].get(key)
                if not record or self.gpu not in record['blocked_by']:continue
                remaining=[gpu for gpu in record['blocked_by'] if gpu!=self.gpu]
                if remaining:
                    record['blocked_by']=remaining;self._save(state);continue
                try:
                    rc,out=run(self.command(item,'start'),90)
                    if rc:raise RecoveryError(out)
                    del state['items'][key];self._save(state)
                except RecoveryError as error:errors.append(f'{item}: {error}')
        if errors:raise RecoveryError('Service restore failed: '+'; '.join(errors))


def verify(target,cfg):
    rc,out=run(['/usr/bin/nvidia-smi','-i',target.bdf,'--query-gpu=uuid,pci.bus_id,name','--format=csv,noheader'])
    if rc or not out: raise RecoveryError(f'NVML health check failed: {out}')
    rows=list(csv.reader(out.splitlines(),skipinitialspace=True))
    if len(rows)!=1 or len(rows[0])!=3: raise RecoveryError('Ambiguous NVML target identity')
    uuid,bus,name=rows[0]
    expected=cfg.get('expected_gpu_uuids',{}).get(target.bdf)
    if expected and uuid!=expected:raise RecoveryError('GPU UUID changed; refusing workload validation and service restoration')
    domain,tail=bus.split(':',1)
    if f'{int(domain,16):04x}:{tail.lower()}'!=target.bdf: raise RecoveryError('NVML returned the wrong GPU')
    validation=cfg.get('validation_command')
    if not validation and cfg.get('cuda_validation',True):
        probe=pathlib.Path(__file__).resolve().with_name('cuda_probe.py')
        trusted_file(probe)
        validation=[sys.executable,str(probe),'--uuid',uuid]
    if validation:
        trusted_file(validation[0])
        rc,result=run(validation,60,dict(os.environ,GPU_BDF=target.bdf,CUDA_VISIBLE_DEVICES=uuid))
        if rc: raise RecoveryError(f'GPU workload validation failed: {result}')
        log(result)
    log('GPU responded to NVML'+(' and workload validation passed' if validation else '; CUDA execution has not been verified'))

def kernel_reset_paths(target,cfg,root=SYSFS):
    mode=cfg.get('kernel_reset_mode','auto')
    if mode not in ('auto','device','bus','acpi'):
        raise RecoveryError('kernel_reset_mode must be auto, device, bus or acpi')
    device=root/target.bdf/'reset'
    subordinate=root/target.bridge/'reset_subordinate' if target.bridge else None
    bus_available=subordinate is not None and subordinate.exists()
    if mode=='acpi':
        if 'acpi' not in target.reset_methods:
            raise RecoveryError('ACPI reset is not listed by the kernel for this device')
        return [device]
    if mode=='bus':
        if not bus_available:raise RecoveryError('Kernel subordinate bus reset is not exposed')
        return [subordinate]
    if mode=='device':
        if not target.reset_methods:raise RecoveryError('No device reset method exposed')
        return [device]
    # An all-ones/zero vendor response cannot carry a targeted PCI FLR.
    raw=config_bytes(root/target.bdf/'config')
    if bus_available and raw[:2] in (b'\xff\xff',b'\x00\x00'):
        return [subordinate]
    return ([device] if target.reset_methods else [])+([subordinate] if bus_available else [])

def detached_reset(target,cfg,root=SYSFS):
    if not target.isolated: raise RecoveryError('PCI reset refused: bridge contains other devices or isolation is unknown')
    if target.drivers.get(target.bdf) not in (None,'nvidia'):
        raise RecoveryError('VFIO/other driver lifecycle is unsupported; supply a verified backend')
    methods=kernel_reset_paths(target,cfg,root)
    if not methods:
        raise RecoveryError('No kernel reset mechanism exposed')
    detached=[];blocked=False;method_override=False
    try:
        # Unbind auxiliary functions first, GPU last; preserve original drivers.
        for bdf in sorted(target.drivers,key=lambda x:x==target.bdf):
            write_sysfs(root/bdf/'driver/unbind',bdf)
            detached.append(bdf)
        if cfg.get('kernel_reset_mode')=='acpi':
            write_sysfs(root/target.bdf/'reset_method','acpi')
            method_override=True
        errors=[]
        for method in methods:
            try:
                log(f'Kernel reset: {method}')
                write_sysfs(method,'1')
                return
            except CommandTimeout: blocked=True;raise
            except RecoveryError as e: errors.append(str(e))
        raise RecoveryError('Kernel reset failed: '+'; '.join(errors))
    except CommandTimeout:
        blocked=True
        raise
    finally:
        # A blocked detach/reset must not be followed by another kernel call
        # against its locked driver/device. Report manual intervention instead.
        if (detached or method_override) and not blocked:
            errors=[]
            if method_override:
                try:write_sysfs(root/target.bdf/'reset_method',' '.join(target.reset_methods))
                except CommandTimeout:raise
                except RecoveryError as e:errors.append(str(e))
            for bdf in reversed(detached):
                try: write_sysfs(root.parent/'drivers'/target.drivers[bdf]/'bind',bdf)
                except RecoveryError as e: errors.append(str(e))
            if errors: raise RecoveryError('Driver rebind failed: '+'; '.join(errors))

def recover(target,cfg,backend_only=False):
    if os.geteuid(): raise RecoveryError('Recovery requires root')
    # Rebuild the inventory for every attempt, including watcher-triggered recovery.
    # No services or PCI state have been changed at this point.
    target=inspect(target.bdf)
    report=explore(target,cfg)
    log('Board exploration: '+json.dumps(report,sort_keys=True))
    cfg=discovered_config(report,cfg)
    log('Selected recovery: '+report['selection'])
    backend=backend_for(target,cfg)
    if backend_only and not backend: raise RecoveryError('No lifecycle backend configured for this GPU')
    if backend:
        argv=backend.get('command')
        if not isinstance(argv,list) or not argv: raise RecoveryError('Backend needs an argument array')
        trusted_file(argv[0])
        if backend.get('requires_single_gpu') and len(discover())!=1:
            raise RecoveryError('This adapter requires a single-GPU host')
        match=backend.get('match',{})
        # Binding a platform adapter requires explicit administrator identity checks.
        if not match: raise RecoveryError('Backend needs explicit identity match constraints')
        for name,value in match.items():
            if name not in ('board_name','board_vendor','product_name'): raise RecoveryError('Unknown backend identity field')
            if text(pathlib.Path('/sys/class/dmi/id')/name)!=value: raise RecoveryError(f'Backend mismatch: {name}')
    if not target.isolated and (backend_only or (backend and backend.get('prefer_backend'))):
        raise RecoveryError('Unknown/shared PCI reset scope; lifecycle backend refused')
    lock=open('/run/gpu-recovery-orchestrator.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    services=Services(service_items(cfg,target.bdf),target.bdf);boot=text('/proc/sys/kernel/random/boot_id')
    verified=False
    try:
        services.stop()
        if not backend_only and not (backend and backend.get('prefer_backend')):
            client_rc,clients=run(['/usr/bin/nvidia-smi','-i',target.bdf,'--query-compute-apps=pid','--format=csv,noheader'],15)
            if client_rc==0 and any(re.fullmatch(r'\d+',line.strip()) for line in clients.splitlines()):
                raise RecoveryError('CUDA clients still use this GPU; configure services or drain them before recovery')
            mode=cfg.get('nvidia_reset_mode','auto')
            if mode not in ('auto','flr','bus'): raise RecoveryError('nvidia_reset_mode must be auto, flr or bus')
            action=(report.get('nvidia',{}).get('recovery_action') or '').lower()
            bus_reset=mode=='bus' or mode=='auto' and action=='bus reset'
            if bus_reset:
                kernel_fallback_allowed(report,len(discover()))
                if not target.isolated: raise RecoveryError('NVIDIA bus reset requires an isolated target bridge')
            argv=['/usr/bin/nvidia-smi','--gpu-reset']+(['bus'] if bus_reset else [])+['-i',target.bdf]
            rc,out=run(argv,30)
            if rc==0:
                log(out);verify(target,cfg);verified=True;return
            log(f'Targeted NVML reset unavailable: {out}')
            if re.search(r'\bin use\b|active process|currently being used',out,re.I):
                raise RecoveryError('NVML reports active users; refusing to bypass its client protection')
            if re.search(r'access blocked|permission denied|insufficient permission',out,re.I):
                raise RecoveryError('NVIDIA reset was denied by OS permissions; no lower-level reset attempted')
        if backend:
            if not target.isolated: raise RecoveryError('Shared/unknown PCI reset scope; backend refused')
            env=dict(os.environ,GPU_BDF=target.bdf,GPU_BRIDGE=target.bridge,
                     GPU_SLOT_FUNCTIONS=json.dumps(target.slot))
            rc,out=run(backend['command'],180,env)
            log(out)
            if rc: raise RecoveryError('Lifecycle backend failed; no further reset attempted')
        else:
            kernel_fallback_allowed(report,len(discover()))
            detached_reset(target,cfg)
        verify(target,cfg);verified=True
    finally:
        if text('/proc/sys/kernel/random/boot_id')!=boot: raise RecoveryError('Boot ID changed unexpectedly')
        if verified:services.restore()
        else:log('Recovery not verified; configured services remain held')

def xid_bdf(message):
    match=re.search(r'Xid\s*\(PCI:([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2})(?:\.[0-7])?\):\s*79\b',message,re.I)
    return match[1].lower()+'.0' if match else None

def nvml_identity(bdf):
    rc,out=run(['/usr/bin/nvidia-smi','-i',bdf,'--query-gpu=uuid,pci.bus_id','--format=csv,noheader'],10)
    if rc:raise RecoveryError('GPU health query failed: '+out)
    rows=list(csv.reader(out.splitlines(),skipinitialspace=True))
    if len(rows)!=1 or len(rows[0])!=2:raise IdentityMismatch('Ambiguous GPU health response')
    uuid,bus=rows[0]
    try:
        domain,tail=bus.split(':',1)
        normalized=f'{int(domain,16):04x}:{tail.lower()}'
    except ValueError:raise IdentityMismatch('Malformed GPU health PCI identity')
    if normalized!=bdf or not re.fullmatch(r'GPU-[0-9a-fA-F-]{36}',uuid):
        raise IdentityMismatch('GPU health identity mismatch')
    return uuid


def resume(bdf,cfg):
    if os.geteuid():raise RecoveryError('Service restoration requires root')
    target=inspect(bdf)
    with open('/run/gpu-recovery-orchestrator.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        effective=discovered_config(explore(target,cfg),cfg)
        verify(target,effective)
        Services(service_items(effective,bdf),bdf).restore()


class Watcher:
    def __init__(self,cfg,budget_path=None):
        self.cfg=dict(cfg);self.identities=dict(cfg.get('expected_gpu_uuids',{}))
        self.cfg['expected_gpu_uuids']=self.identities
        self.failed=set();self.last_end={};self.attempts=collections.defaultdict(collections.deque)
        self.path=budget_path or SERVICE_STATE.with_name('watch-budget.json')
        self.boot=text('/proc/sys/kernel/random/boot_id')
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);trusted_file(self.path.parent)
        if self.path.exists():
            trusted_file(self.path);saved=json.loads(self.path.read_text())
            if saved.get('boot_id')==self.boot:
                for bdf,uuid in saved.get('identities',{}).items():
                    if not BDF.fullmatch(bdf) or not isinstance(uuid,str) or not re.fullmatch(r'GPU-[0-9a-fA-F-]{36}',uuid):raise RecoveryError('Invalid saved GPU identity')
                    self.identities.setdefault(bdf,uuid)
                for bdf,values in saved['attempts'].items():
                    if not BDF.fullmatch(bdf) or not isinstance(values,list) or any(not isinstance(v,(int,float)) for v in values):
                        raise RecoveryError('Invalid watcher budget')
                    self.attempts[bdf]=collections.deque(values)
    def save_budget(self):
        descriptor,name=tempfile.mkstemp(prefix='.watch-',dir=self.path.parent)
        try:
            with os.fdopen(descriptor,'w') as stream:
                json.dump({'boot_id':self.boot,'identities':self.identities,'attempts':{bdf:list(values) for bdf,values in self.attempts.items()}},stream)
                stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)
    def hold(self,bdf):
        try:Services(service_items(self.cfg,bdf),bdf).stop();return True
        except Exception as error:
            self.failed.add(bdf);log(f'{bdf}: cannot hold configured services: {error}');return False
    def check(self,bdf,event=False,event_time=None):
        try:
            uuid=nvml_identity(bdf)
            if bdf in self.identities and self.identities[bdf]!=uuid:
                self.hold(bdf);self.failed.add(bdf)
                log(f'{bdf}: UUID changed; services held, recovery refused');return
            if bdf not in self.identities:
                self.identities[bdf]=uuid;self.save_budget()
            if not event or event_time is not None and event_time<=self.last_end.get(bdf,0):return
        except IdentityMismatch as error:
            self.hold(bdf);self.failed.add(bdf)
            log(f'{bdf}: untrusted health identity; no reset attempted: {error}');return
        except (CommandTimeout,OSError) as error:
            self.hold(bdf);self.failed.add(bdf)
            log(f'{bdf}: blocked/unavailable health command; no reset attempted: {error}');return
        except RecoveryError:pass
        if not self.hold(bdf):return
        now=time.monotonic();history=self.attempts[bdf]
        while history and now-history[0]>=3600:history.popleft()
        if bdf in self.failed or len(history)>=3:
            log(f'{bdf}: recovery suppressed; configured services remain held');return
        history.append(now);self.save_budget()
        try:recover(inspect(bdf),self.cfg)
        except Exception as error:self.failed.add(bdf);log(f'{bdf}: recovery failed; services held: {error}')
        finally:self.last_end[bdf]=time.monotonic()


def watch(cfg):
    if os.geteuid():raise RecoveryError('Watcher requires root')
    allowed=set(cfg.get('watch_gpus',[]))
    if not allowed or any(not isinstance(bdf,str) or not BDF.fullmatch(bdf) for bdf in allowed):
        raise RecoveryError('Explicit watch_gpus PCI allowlist required')
    interval=cfg.get('watch_health_interval',15)
    if not isinstance(interval,(int,float)) or not 5<=interval<=3600:
        raise RecoveryError('watch_health_interval must be 5..3600 seconds')
    events=queue.Queue(maxsize=1024);controller=Watcher(cfg)
    journal=subprocess.Popen(['/usr/bin/journalctl','-k','-f','-n','0','-o','json'],stdout=subprocess.PIPE,text=True)
    try:
        def read():
            for line in journal.stdout:events.put(line)
            events.put(None)
        threading.Thread(target=read,daemon=True).start()
        for bdf in sorted(allowed):controller.check(bdf)
        deadline=time.monotonic()+interval
        while True:
            if time.monotonic()>=deadline:
                for bdf in sorted(allowed):controller.check(bdf)
                deadline=time.monotonic()+interval
            try:line=events.get(timeout=max(.1,deadline-time.monotonic()))
            except queue.Empty:continue
            if line is None:raise RecoveryError('Kernel journal stream ended')
            try:entry=json.loads(line);bdf=xid_bdf(entry.get('MESSAGE',''))
            except ValueError:continue
            if bdf not in allowed:continue
            try:event_time=int(entry['__MONOTONIC_TIMESTAMP'])/1e6
            except (KeyError,ValueError,TypeError):event_time=None
            controller.check(bdf,True,event_time)
    finally:
        if journal.poll() is None:
            journal.terminate()
            try:journal.wait(timeout=5)
            except subprocess.TimeoutExpired:journal.kill();journal.wait(timeout=5)
        journal.stdout.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['discover','explore','plan','auto','recover','watch','hold','resume','check'])
    parser.add_argument('--gpu',help='PCI BDF; automatically selected only when exactly one NVIDIA GPU exists')
    parser.add_argument('--config',help='JSON configuration; root-managed for privileged operations')
    parser.add_argument('--backend-only',action='store_true',help='Skip NVML reset; use configured lifecycle adapter')
    parser.add_argument('--deep',action='store_true',help='Statically decode ACPI reset and power methods using ACPICA iasl')
    args=parser.parse_args();cfg=config(args.config,args.action in ('auto','recover','watch','hold','resume') or args.action=='check' and os.geteuid()==0)
    if args.deep: cfg['deep_discovery']=True
    if args.action=='watch': return watch(cfg)
    if args.action in ('hold','resume'):
        if os.geteuid() or not args.gpu or not BDF.fullmatch(args.gpu):raise RecoveryError('Root and explicit --gpu PCI address required')
        if args.action=='resume':return resume(args.gpu,cfg)
        return Services(service_items(cfg,args.gpu),args.gpu).stop()
    found=discover()
    if args.action=='discover': print(json.dumps(found,indent=2));return
    gpu=args.gpu or (found[0] if len(found)==1 else None)
    if not gpu: raise RecoveryError('Use --gpu to explicitly select one GPU')
    target=inspect(gpu)
    if args.action=='check':return verify(target,cfg)
    if args.action in ('explore','plan'):
        print(json.dumps(explore(target,cfg),indent=2));return
    recover(target,cfg,args.backend_only)

if __name__=='__main__':
    try: main()
    except Exception as e: log(str(e));sys.exit(1)

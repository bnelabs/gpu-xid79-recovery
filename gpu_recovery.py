#!/usr/bin/env python3
"""Discover and recover NVIDIA GPUs using host-exposed reset capabilities."""
import argparse, collections, csv, dataclasses, fcntl, hashlib, json, os, pathlib, pwd, re, signal, subprocess, sys, time

BDF = re.compile(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]')
SYSFS = pathlib.Path('/sys/bus/pci/devices')
ORPHANS = []
PROFILE_DIR = pathlib.Path('/etc/gpu-recovery/profiles.d')
DMI = pathlib.Path('/sys/class/dmi/id')

class RecoveryError(RuntimeError): pass
class CommandTimeout(RecoveryError): pass

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
        if BDF.fullmatch(p.name) and text(p/'vendor')=='0x10de' and text(p/'class').startswith('0x03'):
            result.append(p.name)
    return result

def inspect(bdf,root=SYSFS):
    if not BDF.fullmatch(bdf) or bdf not in discover(root): raise RecoveryError('Select a discovered NVIDIA display-class GPU')
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
        elif cap==0x10:
            item['name']='PCI Express';flags=number(pointer+2,2);devcap=number(pointer+4,4)
            item['port_type']=(flags>>4)&15 if flags is not None else None
            item['flr_advertised']=bool(devcap&(1<<28)) if devcap is not None else None
            link=number(pointer+0x12,2)
            item['link_status']=hex(link) if link is not None else None
        elif cap in (5,0x11): item['name']='MSI' if cap==5 else 'MSI-X'
        result['standard'].append(item);pointer=data[pointer+1]&0xfc
    pointer=0x100;seen=set()
    names={1:'AER',0xb:'Vendor specific',0xd:'ACS',0x10:'SR-IOV',0x15:'Resizable BAR',0x1d:'DPC'}
    while pointer>=0x100 and pointer%4==0 and pointer+4<=len(data) and pointer not in seen:
        seen.add(pointer);value=number(pointer,4)
        if value in (0,0xffffffff): break
        cap=value&0xffff
        result['extended'].append({'id':cap,'name':names.get(cap,'Other'),
                                   'version':(value>>16)&15,'offset':pointer})
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
            finally: os.close(fd)
        except OSError as e: item['unavailable']=str(e)
        chips.append(item)
    return chips

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
            'profiles':[],'selected_backend':backend_for(target,cfg),
            'unverified_candidates_policy':'Inventory only; GPIO wiring and firmware reset scope require a verified adapter'}
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

class Services:
    def __init__(self,items): self.items=items;self.stopped=[]
    def command(self,item,verb):
        unit=item['unit']
        if not re.fullmatch(r'[A-Za-z0-9_.@:\\-]+\.service',unit): raise RecoveryError('Invalid service unit')
        if 'user' not in item: return ['/usr/bin/systemctl',verb,unit]
        user=pwd.getpwnam(item['user']);uid=user.pw_uid
        return ['/usr/sbin/runuser','-u',user.pw_name,'--','/usr/bin/env',
                f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',
                '/usr/bin/systemctl','--user',verb,unit]
    def stop(self):
        for item in self.items:
            active,_=run(self.command(item,'is-active'))
            if active==0:
                self.stopped.append(item)
                rc,out=run(self.command(item,'stop'),30)
                if rc: raise RecoveryError(f'Could not drain {item}: {out}')
    def restore(self):
        errors=[]
        for item in reversed(self.stopped):
            try:
                rc,out=run(self.command(item,'start'),30)
                if rc: errors.append(f'{item}: {out}')
            except RecoveryError as e: errors.append(str(e))
        if errors: raise RecoveryError('Service restore failed: '+'; '.join(errors))

def verify(target,cfg):
    rc,out=run(['/usr/bin/nvidia-smi','-i',target.bdf,'--query-gpu=uuid,pci.bus_id,name','--format=csv,noheader'])
    if rc or not out: raise RecoveryError(f'NVML health check failed: {out}')
    rows=list(csv.reader(out.splitlines(),skipinitialspace=True))
    if len(rows)!=1 or len(rows[0])!=3: raise RecoveryError('Ambiguous NVML target identity')
    uuid,bus,name=rows[0]
    domain,tail=bus.split(':',1)
    if f'{int(domain,16):04x}:{tail.lower()}'!=target.bdf: raise RecoveryError('NVML returned the wrong GPU')
    validation=cfg.get('validation_command')
    if validation:
        trusted_file(validation[0])
        rc,result=run(validation,60,dict(os.environ,GPU_BDF=target.bdf,CUDA_VISIBLE_DEVICES=uuid))
        if rc: raise RecoveryError(f'GPU workload validation failed: {result}')
        log(result)
    log('GPU responded to NVML'+(' and workload validation passed' if validation else '; CUDA execution has not been verified'))

def detached_reset(target,cfg,root=SYSFS):
    if not target.isolated: raise RecoveryError('PCI reset refused: bridge contains other devices or isolation is unknown')
    if target.drivers.get(target.bdf) not in (None,'nvidia'):
        raise RecoveryError('VFIO/other driver lifecycle is unsupported; supply a verified backend')
    if not target.reset_methods and not (root/target.bridge/'reset_subordinate').exists():
        raise RecoveryError('No kernel reset mechanism exposed')
    detached=[];blocked=False
    try:
        # Unbind auxiliary functions first, GPU last; preserve original drivers.
        for bdf in sorted(target.drivers,key=lambda x:x==target.bdf):
            write_sysfs(root/bdf/'driver/unbind',bdf)
            detached.append(bdf)
        methods=[]
        if target.reset_methods: methods.append(root/target.bdf/'reset')
        subordinate=root/target.bridge/'reset_subordinate'
        if subordinate.exists(): methods.append(subordinate)
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
        if detached and not blocked:
            errors=[]
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
    services=Services(cfg.get('services',[]));boot=text('/proc/sys/kernel/random/boot_id')
    try:
        services.stop()
        if not backend_only and not (backend and backend.get('prefer_backend')):
            client_rc,clients=run(['/usr/bin/nvidia-smi','-i',target.bdf,'--query-compute-apps=pid','--format=csv,noheader'],15)
            if client_rc==0 and any(re.fullmatch(r'\d+',line.strip()) for line in clients.splitlines()):
                raise RecoveryError('CUDA clients still use this GPU; configure services or drain them before recovery')
            rc,out=run(['/usr/bin/nvidia-smi','--gpu-reset','-i',target.bdf],30)
            if rc==0:
                log(out);verify(target,cfg);return
            log(f'Targeted NVML reset unavailable: {out}')
            if re.search(r'\bin use\b|active process|currently being used',out,re.I):
                raise RecoveryError('NVML reports active users; refusing to bypass its client protection')
        if backend:
            if not target.isolated: raise RecoveryError('Shared/unknown PCI reset scope; backend refused')
            env=dict(os.environ,GPU_BDF=target.bdf,GPU_BRIDGE=target.bridge,
                     GPU_SLOT_FUNCTIONS=json.dumps(target.slot))
            rc,out=run(backend['command'],180,env)
            log(out)
            if rc: raise RecoveryError('Lifecycle backend failed; no further reset attempted')
        else:
            detached_reset(target,cfg)
        verify(target,cfg)
    finally:
        services.restore()
        if text('/proc/sys/kernel/random/boot_id')!=boot: raise RecoveryError('Boot ID changed unexpectedly')

def xid_bdf(message):
    match=re.search(r'Xid\s*\(PCI:([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2})(?:\.[0-7])?\):\s*79\b',message,re.I)
    return match[1].lower()+'.0' if match else None

def watch(cfg):
    if os.geteuid(): raise RecoveryError('Watcher requires root')
    allowed=set(cfg.get('watch_gpus',[]))
    if not allowed: raise RecoveryError('Explicit watch_gpus allowlist required')
    attempts=collections.defaultdict(collections.deque);failed=set()
    with subprocess.Popen(['/usr/bin/journalctl','-k','-f','-n','0','-o','json'],stdout=subprocess.PIPE,text=True) as journal:
        for line in journal.stdout:
            try: gpu=xid_bdf(json.loads(line).get('MESSAGE',''))
            except ValueError: continue
            if gpu not in allowed: continue
            now=time.time();history=attempts[gpu]
            while history and now-history[0]>3600: history.popleft()
            if gpu in failed or len(history)>=3:
                log(f'{gpu}: suppressed after failure or three attempts/hour');continue
            history.append(now)
            try: recover(inspect(gpu),cfg)
            except Exception as e: failed.add(gpu);log(f'{gpu}: {e}')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['discover','explore','plan','auto','recover','watch'])
    parser.add_argument('--gpu',help='PCI BDF; automatically selected only when exactly one NVIDIA GPU exists')
    parser.add_argument('--config',help='JSON configuration; root-managed for privileged operations')
    parser.add_argument('--backend-only',action='store_true',help='Skip NVML reset; use configured lifecycle adapter')
    args=parser.parse_args();cfg=config(args.config,args.action in ('auto','recover','watch'))
    if args.action=='watch': return watch(cfg)
    found=discover()
    if args.action=='discover': print(json.dumps(found,indent=2));return
    gpu=args.gpu or (found[0] if len(found)==1 else None)
    if not gpu: raise RecoveryError('Use --gpu to explicitly select one GPU')
    target=inspect(gpu)
    if args.action in ('explore','plan'):
        print(json.dumps(explore(target,cfg),indent=2));return
    recover(target,cfg,args.backend_only)

if __name__=='__main__':
    try: main()
    except Exception as e: log(str(e));sys.exit(1)

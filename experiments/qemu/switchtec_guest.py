#!/usr/bin/env python3
"""Run production switch adapter against a guest-only physical-port model."""
import json
import mmap
import os
import pathlib
import sys
import traceback
sys.path.insert(0, '/lab')
import gpu_recovery as g
import enterprise_fixture as fixture
import recovery_capsule


def main():
    if 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
        raise RuntimeError('Disposable guest required')
    devices = sorted(p for p in g.SYSFS.iterdir()
        if g.text(p/'vendor') == '0x1b36' and g.text(p/'device') == '0x0005')
    if len(devices) != 8:
        raise RuntimeError('Expected eight synthetic endpoints')
    bdfs = [p.name for p in devices]
    approved = {bdf: 'GPU-'+f'{i+1:08x}-2222-3333-4444-555555555555' for i,bdf in enumerate(bdfs)}
    parents = {p.name: str(p.resolve().parent) for p in devices}
    ports = {str(i): parent for i, parent in enumerate(sorted(set(parents.values())))}
    gpu_ports = {bdf: next(k for k,v in ports.items() if v == parent) for bdf,parent in parents.items()}
    native = g.text
    def text(path, *args):
        p = pathlib.Path(path)
        if p == g.DMI/'product_name':return 'Switchtec synthetic fixture'
        if p.parent.name in bdfs and p.name in ('vendor','class'):
            return '0x10de' if p.name == 'vendor' else '0x030000'
        return native(path,*args)
    g.text = text
    g.discover = lambda root=g.SYSFS: [p.name for p in devices if p.exists()]
    g.nvml_identity = lambda bdf: approved[bdf]
    regions = {}
    def remap():
        for p in devices:
            old = regions.pop(p.name, None)
            if old is not None:old.close()
            with (p/'config').open('r+b',buffering=0) as stream:
                stream.seek(4); value=int.from_bytes(stream.read(2),'little')
                stream.seek(4);stream.write((value|2).to_bytes(2,'little'))
            fd = os.open(p/'resource0',os.O_RDWR)
            try:regions[p.name] = mmap.mmap(fd,4096)
            finally:os.close(fd)
    def counts():return {bdf:int.from_bytes(r[12:16],'little') for bdf,r in regions.items()}
    verified=[]
    def verify(target,cfg):
        if not verified:remap()
        if counts()[target.bdf] != 0:raise g.RecoveryError('Counter workload failed')
        if fixture.state()['fault'] == 'workload-failed' and target.bdf == bdfs[-1]:
            raise g.RecoveryError('Injected member workload failure')
        verified.append(target.bdf)
    g.verify=verify
    path=pathlib.Path('/lab/switchtec.json')
    adapter={'schema':1,'controller_id':'LAB-PAX','device':'LAB-SIDEBAND',
        'state_dir':'/run/switchtec-journal','reset_hold_seconds':.01,
        'switchtec_command':['/usr/bin/python3','/lab/switchtec_fixture.py'],
        'lifecycle_command':['/usr/bin/python3','/lab/switchtec_fixture.py'],
        'targets':{bdf:{'uuid':uuid,'physical_port':int(gpu_ports[bdf])} for bdf,uuid in approved.items()}}
    path.write_text(json.dumps(adapter));path.chmod(0o600)
    command=['/usr/bin/python3','/lab/adapters/switchtec_gpu.py','--config',str(path)]
    backend={'prefer_backend':True,'command':command,'match':{'product_name':'Switchtec synthetic fixture'},
        'domain':{'members':approved,'preflight_command':command,'validation_command':command}}
    units=['shared-worker.service',*[f'worker-{i}.service' for i in range(8)]]
    cfg={'watch_gpus':bdfs,'expected_gpu_uuids':approved,'backends':{b:backend for b in bdfs},
         'services':[{'unit':units[0]}],'gpu_services':{b:[{'unit':units[i+1]}] for i,b in enumerate(bdfs)}}
    storage=next(p for p in g.SYSFS.iterdir() if native(p/'class').startswith('0x0108'))
    storage_before=(storage/'config').read_bytes()[:64]
    boot=native('/proc/sys/kernel/random/boot_id')
    journal=pathlib.Path('/run/switchtec-journal/operations.json')
    results=[]
    for fault in ['success','wrong-port-map','no-independent-control','set-response-lost',
                  'clear-failed','restart-blocked','reconcile-resume','workload-failed',
                  'fabric-failed','all-missing']:
        for parent in ports.values():
            g.write_sysfs(g.subordinate_rescan_path(pathlib.Path(parent).name),'1')
        remap()
        for r in regions.values():r[0]=0;r[2048]=0xfa
        if set(counts().values()) != {1}:raise RuntimeError('Counter seeding failed')
        verified.clear();g.SERVICE_STATE.unlink(missing_ok=True);journal.unlink(missing_ok=True)
        fixture.EVENTS.write_text('')
        service_dir=pathlib.Path('/run/enterprise-services');service_dir.mkdir(exist_ok=True)
        for unit in units:(service_dir/unit).touch()
        injected='set-response-lost' if fault in ('restart-blocked','reconcile-resume') else fault
        fixture.STATE.write_text(json.dumps(dict(fault=injected,members=approved,parents=parents,
            ports=ports,gpu_ports=gpu_ports,asserted=[],services={u:True for u in units})))
        backend['domain'].pop('capsule',None)
        missing=[]
        if fault == 'all-missing':
            backend['domain']['capsule']='/var/lib/gpu-recovery-generic/switchtec-capsule.json'
            recovery_capsule.capture(g,cfg,backend)
            for p in devices:g.write_sysfs(p/'remove','1')
            missing=[p.name for p in devices if not p.exists()]
            if missing != bdfs:raise RuntimeError('Endpoints not removed')
        if fault in ('restart-blocked','reconcile-resume'):
            try:g.recover_bdf(bdfs[0],cfg)
            except g.RecoveryError:pass
            else:raise RuntimeError('Lost response unexpectedly succeeded')
            if fault == 'reconcile-resume':
                # Independent operator reconciliation, never an automatic retry.
                for parent in ports.values():g.write_sysfs(pathlib.Path(parent)/'reset_subordinate','1')
                state=fixture.state();state.update(fault='success',asserted=[])
                fixture.STATE.write_text(json.dumps(state))
        error=None
        try:
            if fault == 'reconcile-resume':g.resume(bdfs[0],cfg)
            else:g.recover_bdf(bdfs[0],cfg)
        except g.RecoveryError as exc:error=str(exc)
        events=[json.loads(line) for line in fixture.EVENTS.read_text().splitlines()]
        commands=[e for e in events if e['kind']=='switch-command']
        starts=[e for e in events if e.get('verb')=='start']
        ledger=json.loads(g.SERVICE_STATE.read_text())['items'] if g.SERVICE_STATE.exists() else {}
        pending=len(json.loads(journal.read_text())['operations']) if journal.exists() else 0
        success=fault in ('success','all-missing','reconcile-resume')
        expected={'wrong-port-map':'switchtec_targets','no-independent-control':'independent_reset_control',
            'set-response-lost':'Domain adapter failed','clear-failed':'Domain adapter failed',
            'restart-blocked':'Unresolved management reset','workload-failed':'Injected member workload failure',
            'fabric-failed':'fabric_verified'}
        if success:
            passed=error is None and verified==bdfs and len(starts)==9 and not ledger and not pending
            passed=passed and set(counts().values())=={0} and len(commands)==(1 if fault=='reconcile-resume' else 8)
            validation=max(i for i,e in enumerate(events) if e.get('phase')=='validation')
            passed=passed and all(i>validation for i,e in enumerate(events) if e.get('verb')=='start')
        else:
            passed=expected[fault] in (error or '') and not starts and bool(ledger)
            passed=passed and all(not (service_dir/u).exists() for u in units)
            n={'wrong-port-map':0,'no-independent-control':0,'set-response-lost':1,
               'clear-failed':2,'restart-blocked':1,'workload-failed':8,'fabric-failed':8}[fault]
            passed=passed and len(commands)==n and pending==(0 if n==0 else 1)
        passed=passed and boot==native('/proc/sys/kernel/random/boot_id') and (storage/'config').read_bytes()[:64]==storage_before
        results.append(dict(fault=fault,passed=bool(passed),error=error,commands=commands,
            verified_members=len(verified),service_starts=len(starts),pending_operations=pending,
            initially_missing_functions=missing,boot_unchanged=boot==native('/proc/sys/kernel/random/boot_id')))
        print('SWITCHTEC_CASE='+json.dumps(results[-1]),flush=True)
    print('GPU_QEMU_RESULT='+json.dumps(dict(passed=all(c['passed'] for c in results),cases=results,
        architecture=os.uname().machine,kernel=os.uname().release,nvidia_recovery_verified=False,
        substitutions=['Synthetic PCI endpoints and counter workloads; no NVIDIA firmware/CUDA',
        'Modeled Switchtec CLI and physical-port controller; no switch firmware',
        'CLEAR performs actual guest-kernel bridge reset; modeled SET persists assertion',
        'Fixture lifecycle and systemctl; production controller, adapter, journal and capsules'])),flush=True)


if __name__=='__main__':
    try:main()
    except Exception:print('GPU_QEMU_RESULT='+json.dumps(dict(passed=False,error=traceback.format_exc())),flush=True)

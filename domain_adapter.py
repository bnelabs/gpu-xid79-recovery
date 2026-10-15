"""Orchestrate an explicitly approved local GPU domain through a trusted adapter."""
import fcntl
import json
import os
import pathlib
import re
import secrets

UUID=re.compile(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def members(g,backend):
    domain=backend.get('domain',{})
    approved=domain.get('members')
    if not isinstance(approved,dict) or not approved:
        raise g.RecoveryError('Domain requires explicit PCI-to-UUID members')
    for bdf,uuid in approved.items():
        if not isinstance(bdf,str) or not g.BDF.fullmatch(bdf) or not isinstance(uuid,str) or not UUID.fullmatch(uuid):
            raise g.RecoveryError('Invalid domain member identity')
    if len(set(approved.values()))!=len(approved):raise g.RecoveryError('Duplicate domain GPU UUID')
    return dict(approved)


def inventory(g,approved,root):
    targets={bdf:g.inspect(bdf,root) for bdf in approved}
    scope={function for target in targets.values() for function in target.slot}
    for function in scope:
        klass=g.text(root/function/'class')
        if g.text(root/function/'vendor')!='0x10de' or not klass.startswith(('0x03','0x04','0x0c03','0x0c80','0x1200')):
            raise g.RecoveryError('Domain slot contains a non-GPU/unsupported auxiliary function')
    snapshots={}
    for bdf,target in targets.items():
        if not target.bridge:raise g.RecoveryError('Domain member lacks a PCI parent')
        path=(root/bdf).resolve();parent=path.parent
        descendants={p.name for p in parent.rglob('*') if g.BDF.fullmatch(p.name)}
        if not descendants<=scope:
            raise g.RecoveryError('Domain parent contains unapproved devices or bridges')
        snapshots[bdf]={'path':str(path),'bridge':target.bridge,'slot':target.slot,
                        'identity':{key:g.text(root/bdf/key) for key in ('vendor','device','subsystem_vendor','subsystem_device')}}
    return targets,sorted(scope),snapshots


def receipt(g,argv,env,approved,scope,boot,nonce,phase):
    rc,out=g.run(argv,60,env)
    if rc:raise g.RecoveryError(f'Domain {phase} command failed; no further reset attempted: {out}')
    try:value=json.loads(out)
    except (ValueError,TypeError):raise g.RecoveryError(f'Domain {phase} did not return a JSON receipt')
    if not isinstance(value,dict):raise g.RecoveryError('Domain receipt must be an object')
    required={'schema':1,'nonce':nonce,'boot_id':boot,'members':approved,
              'scope_bdfs':scope,'exclusive_local_control':True,'host_reboot_required':False,
              'clients_quiesced':True,'ready':True}
    if phase=='validation':required.update(workloads_verified=True,fabric_verified=True)
    for key,expected in required.items():
        actual=value.get(key)
        if type(actual) is not type(expected) or actual!=expected:
            raise g.RecoveryError(f'Domain {phase} receipt mismatch: {key}')
    return value


def execute(g,target,cfg,backend,reset=True,root=None):
    root=g.SYSFS if root is None else root
    approved=members(g,backend)
    if target.bdf not in approved:raise g.RecoveryError('Target not approved in domain')
    if backend.get('prefer_backend') is not True:raise g.RecoveryError('Domain requires prefer_backend=true')
    if backend.get('requires_single_gpu') and len(g.discover(root))!=1:
        raise g.RecoveryError('Domain adapter requires a single-GPU host')
    domain=backend['domain']
    constraints=backend.get('match')
    if not isinstance(constraints,dict) or not constraints:raise g.RecoveryError('Domain adapter requires explicit platform identity constraints')
    for name,value in constraints.items():
        if not isinstance(value,str) or not value.strip() or name not in ('board_name','board_vendor','product_name') or g.text(g.DMI/name)!=value:
            raise g.RecoveryError('Domain platform identity mismatch')
    commands=[backend.get('command'),domain.get('preflight_command'),domain.get('validation_command')]
    for argv in commands:
        if not isinstance(argv,list) or not argv or not all(isinstance(arg,str) for arg in argv):
            raise g.RecoveryError('Domain stages require nonempty argument arrays')
        g.trusted_file(argv[0])
    expected=cfg.get('expected_gpu_uuids',{})
    if any(bdf in expected and expected[bdf]!=uuid for bdf,uuid in approved.items()):
        raise g.RecoveryError('Domain contradicts configured GPU UUID')
    effective=dict(cfg,expected_gpu_uuids={**expected,**approved})
    with open('/run/gpu-recovery-orchestrator.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        targets,scope,snapshots=inventory(g,approved,root)
        boot=g.text('/proc/sys/kernel/random/boot_id')
        if not boot:raise g.RecoveryError('Missing boot identity')
        nonce=secrets.token_hex(16)
        env=dict(os.environ,GPU_BDF=target.bdf,GPU_DOMAIN_MEMBERS=json.dumps(approved,sort_keys=True),
                 GPU_DOMAIN_SCOPE=json.dumps(scope),GPU_RECOVERY_BOOT_ID=boot,GPU_RECOVERY_NONCE=nonce)
        services=[g.Services(g.service_items(cfg,bdf),bdf) for bdf in sorted(approved)]
        verified=False
        try:
            for service in services:service.stop()
            receipt(g,commands[1],dict(env,GPU_RECOVERY_PHASE='preflight'),approved,scope,boot,nonce,'preflight')
            g.log('Domain preflight accepted: '+', '.join(sorted(approved)))
            if inventory(g,approved,root)[2]!=snapshots:raise g.RecoveryError('Domain topology changed after preflight')
            if g.text('/proc/sys/kernel/random/boot_id')!=boot:raise g.RecoveryError('Boot changed before domain action')
            if reset:
                rc,out=g.run(commands[0],180,dict(env,GPU_RECOVERY_PHASE='recover'))
                g.log(out)
                if rc:raise g.RecoveryError('Domain adapter failed; no fallback attempted')
            # Validate all physical identities/workloads before releasing any hold.
            if inventory(g,approved,root)[2]!=snapshots:raise g.RecoveryError('Domain topology changed during recovery')
            for bdf in sorted(approved):g.verify(g.inspect(bdf,root),effective)
            receipt(g,commands[2],dict(env,GPU_RECOVERY_PHASE='validation'),approved,scope,boot,nonce,'validation')
            if g.text('/proc/sys/kernel/random/boot_id')!=boot:raise g.RecoveryError('Boot changed during domain recovery')
            if inventory(g,approved,root)[2]!=snapshots:raise g.RecoveryError('Domain topology changed during validation')
            g.log('All domain members and adapter validation passed')
            verified=True
        finally:
            if not verified:g.log('Domain recovery unverified; service holds retained without automatic restoration')
        for service in reversed(services):service.restore()
    return sorted(approved)

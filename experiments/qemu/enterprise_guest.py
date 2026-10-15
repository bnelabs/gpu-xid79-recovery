#!/usr/bin/env python3
"""Eight-device fault campaign with production controller and Redfish adapter."""
import hashlib
import json
import mmap
import os
import pathlib
import sys
import traceback

sys.path.insert(0, '/lab')
import gpu_recovery as g
import enterprise_fixture as fixture


def main():
    if 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
        raise RuntimeError('Disposable guest kernel marker required')
    devices = sorted(p for p in g.SYSFS.iterdir()
                     if g.text(p/'vendor') == '0x1b36' and g.text(p/'device') == '0x0005')
    if len(devices) != 8:
        raise RuntimeError('Expected eight QEMU test endpoints')
    bdfs = [p.name for p in devices]
    approved = {bdf: 'GPU-' + f'{index+1:08x}-2222-3333-4444-555555555555'
                for index, bdf in enumerate(bdfs)}
    g.discover = lambda root=g.SYSFS: list(bdfs)
    native_text = g.text
    def fixture_text(path, *args):
        path = pathlib.Path(path)
        if path == g.DMI/'product_name':
            return 'Synthetic enterprise fixture'
        if path.parent.name in bdfs and path.name in ('vendor', 'class'):
            return '0x10de' if path.name == 'vendor' else '0x030000'
        return native_text(path, *args)
    g.text = fixture_text
    regions = {}
    for device in devices:
        with (device/'config').open('r+b', buffering=0) as stream:
            stream.seek(4)
            command = int.from_bytes(stream.read(2), 'little')
            stream.seek(4)
            stream.write((command | 2).to_bytes(2, 'little'))
        fd = os.open(device/'resource0', os.O_RDWR)
        try:
            regions[device.name] = mmap.mmap(fd, 4096)
        finally:
            os.close(fd)
    def counts():
        return {bdf: int.from_bytes(region[12:16], 'little') for bdf, region in regions.items()}
    def seed():
        for region in regions.values():
            region[0] = 0
            region[2048] = 0xfa
        if set(counts().values()) != {1}:
            raise RuntimeError('Fixture counters not seeded')
    verified = []
    def verify(target, cfg):
        if cfg['expected_gpu_uuids'] != approved or counts()[target.bdf] != 0:
            raise g.RecoveryError('Synthetic member workload check failed')
        if fixture.state()['fault'] == 'workload-failed' and target.bdf == bdfs[-1]:
            raise g.RecoveryError('Injected member workload failure')
        verified.append(target.bdf)
    g.verify = verify
    server = fixture.serve()
    adapter_config = pathlib.Path('/lab/redfish.json')
    adapter_config.write_text(json.dumps({'schema': 1, 'lab': True,
        'endpoint': f'http://127.0.0.1:{server.server_port}', 'task_timeout': .5,
        'state_dir': '/run/enterprise-reset-journal',
        'lifecycle_command': ['/usr/bin/python3', '/lab/enterprise_fixture.py'],
        'targets': {bdf: {'uuid': approved[bdf], 'serial': f'LAB-GPU-{i}',
            'resource': f'/redfish/v1/Systems/Lab/Processors/GPU{i}', 'reset_type': 'ForceRestart'}
            for i, bdf in enumerate(bdfs)}}))
    adapter_config.chmod(0o600)
    command = ['/usr/bin/python3', '/lab/adapters/redfish_gpu.py', '--config', str(adapter_config)]
    backend = {'command': command, 'prefer_backend': True,
        'match': {'product_name': 'Synthetic enterprise fixture'},
        'domain': {'members': approved, 'preflight_command': command, 'validation_command': command}}
    cfg = {'watch_gpus': bdfs, 'expected_gpu_uuids': approved,
        'backends': {bdf: backend for bdf in bdfs},
        'services': [{'unit': 'shared-fabric-worker.service'}],
        'gpu_services': {bdf: [{'unit': f'worker-{i}.service'}] for i, bdf in enumerate(bdfs)}}
    units = ['shared-fabric-worker.service', *[f'worker-{i}.service' for i in range(8)]]
    # Observe unrelated guest NVMe configuration throughout the campaign.
    storage = sorted(p for p in g.SYSFS.iterdir() if native_text(p/'class').startswith('0x0108'))
    if len(storage) != 1:
        raise RuntimeError('Expected unrelated NVMe controller')
    storage_path = storage[0]
    storage_identity = (storage_path/'config').read_bytes()[:64]
    boot = native_text('/proc/sys/kernel/random/boot_id')
    shared_storage = 'lab_storage_shared=1' in g.text('/proc/cmdline').split()
    cases = ['storage-scope'] if shared_storage else [
        'success', 'wrong-serial', 'cpu-resource', 'node-action', 'unsupported',
        'ownership-denied', 'partial-reset', 'task-failed', 'task-timeout',
        'dropped-response', 'workload-failed', 'fabric-failed', 'stale-validation', 'resume',
        'restart-blocked', 'reconcile-resume', 'failed-reconciliation']
    results = []
    for fault in cases:
        seed()
        verified.clear()
        g.SERVICE_STATE.unlink(missing_ok=True)
        journal_path = pathlib.Path('/run/enterprise-reset-journal/operations.json')
        journal_path.unlink(missing_ok=True)
        fixture.EVENTS.write_text('')
        service_dir = pathlib.Path('/run/enterprise-services')
        service_dir.mkdir(exist_ok=True)
        for unit in units:
            (service_dir/unit).touch()
        injected = 'dropped-response' if fault in ('restart-blocked', 'reconcile-resume', 'failed-reconciliation') else fault
        fixture.STATE.write_text(json.dumps({'fault': injected, 'members': approved,
                                            'services': {unit: True for unit in units}}))
        error = None
        before = counts()
        if fault == 'resume':
            # Model an externally restored domain: resume must validate and
            # release persistent holds without posting a reset.
            for bdf in bdfs:
                g.Services(g.service_items(cfg, bdf), bdf).stop()
            for parent in sorted({p.resolve().parent for p in devices}):
                g.write_sysfs(parent/'reset_subordinate', '1')
        if fault in ('restart-blocked', 'reconcile-resume', 'failed-reconciliation'):
            try:
                g.recover(g.inspect(bdfs[0]), cfg)
                raise RuntimeError('Lost response unexpectedly recovered')
            except g.RecoveryError:
                pass
            initial_events = [json.loads(line) for line in fixture.EVENTS.read_text().splitlines()]
            if len([e for e in initial_events if e['kind'] == 'reset']) != 1 or not journal_path.exists():
                raise RuntimeError('Lost response did not leave exactly one persisted reset intent')
            updated = fixture.state()
            updated['fault'] = 'fabric-failed' if fault == 'failed-reconciliation' else 'success'
            fixture.STATE.write_text(json.dumps(updated))
            if fault in ('reconcile-resume', 'failed-reconciliation'):
                for parent in sorted({p.resolve().parent for p in devices}):
                    g.write_sysfs(parent/'reset_subordinate', '1')
        try:
            if fault in ('resume', 'reconcile-resume', 'failed-reconciliation'):
                g.resume(bdfs[0], cfg)
            else:
                g.recover(g.inspect(bdfs[0]), cfg)
        except g.RecoveryError as exc:
            error = str(exc)
        events = [json.loads(line) for line in fixture.EVENTS.read_text().splitlines()]
        resets = [e for e in events if e['kind'] == 'reset']
        starts = [e for e in events if e.get('verb') == 'start']
        after = counts()
        services = {unit: (service_dir/unit).exists() for unit in units}
        ledger = json.loads(g.SERVICE_STATE.read_text())['items'] if g.SERVICE_STATE.exists() else {}
        same_boot = boot == native_text('/proc/sys/kernel/random/boot_id')
        storage_unchanged = storage_path.exists() and (storage_path/'config').read_bytes()[:64] == storage_identity
        success = fault in ('success', 'resume', 'reconcile-resume')
        if success:
            passed = (error is None and set(after.values()) == {0} and verified == bdfs
                      and all(services.values()) and not ledger and len(starts) == 9
                      and len(resets) == (8 if fault == 'success' else 1 if fault == 'reconcile-resume' else 0))
        elif fault == 'storage-scope':
            passed = bool(error) and not resets and before == after and not starts
        else:
            passed = bool(error) and not any(services.values()) and not starts and bool(ledger)
            passed = passed and all(set(record['blocked_by']) <= set(bdfs) for record in ledger.values())
            if fault in ('wrong-serial', 'cpu-resource', 'node-action', 'unsupported', 'ownership-denied'):
                passed = passed and not resets and before == after
            elif fault in ('task-failed', 'task-timeout', 'dropped-response'):
                passed = passed and len(resets) == 1
            elif fault == 'partial-reset':
                passed = passed and len(resets) == 3
        expected_errors = {
            'wrong-serial': 'GPU serial differs', 'cpu-resource': 'not the expected GPU Processor',
            'node-action': 'escapes the mapped GPU resource', 'unsupported': 'not advertised',
            'ownership-denied': 'exclusive_local_control', 'partial-reset': 'Domain adapter failed',
            'task-failed': 'Domain adapter failed', 'task-timeout': 'Domain adapter failed',
            'dropped-response': 'Domain adapter failed', 'workload-failed': 'Injected member workload failure',
            'fabric-failed': 'fabric_verified', 'stale-validation': 'nonce',
            'storage-scope': 'unapproved devices or bridges',
            'restart-blocked': 'Unresolved management reset',
            'failed-reconciliation': 'fabric_verified'}
        if not success:
            passed = passed and expected_errors[fault] in (error or '')
        if fault in ('fabric-failed', 'stale-validation'):
            passed = passed and verified == bdfs and len(resets) == 8
        if fault == 'workload-failed':
            passed = passed and verified == bdfs[:-1] and len(resets) == 8
        if success and error is None:
            validation_index = max(i for i, e in enumerate(events) if e.get('phase') == 'validation')
            passed = passed and all(i > validation_index for i, e in enumerate(events) if e.get('verb') == 'start')
        pending_operations = len(json.loads(journal_path.read_text())['operations']) if journal_path.exists() else 0
        if fault in ('restart-blocked', 'failed-reconciliation'):
            passed = passed and len(resets) == 1 and pending_operations == 1
        if fault == 'failed-reconciliation':
            passed = passed and verified == bdfs
        if success:
            passed = passed and pending_operations == 0
        print('ENTERPRISE_CASE=' + json.dumps({'fault': fault, 'error': error, 'passed': bool(passed)}), flush=True)
        results.append({'fault': fault, 'passed': bool(passed and same_boot and storage_unchanged),
            'error': error, 'reset_posts': len(resets), 'service_starts': len(starts),
            'held_records': len(ledger), 'verified_members': len(verified),
            'unresolved_operations': pending_operations,
            'before': before, 'after': after, 'boot_unchanged': same_boot,
            'storage_unchanged': storage_unchanged})
    server.shutdown()
    for region in regions.values():
        region.close()
    print('GPU_QEMU_RESULT=' + json.dumps({'kind': 'Eight-device enterprise orchestration fault campaign',
        'architecture': os.uname().machine, 'kernel': os.uname().release,
        'members': approved, 'cases': results, 'passed': all(r['passed'] for r in results),
        'nvidia_recovery_verified': False,
        'substitutions': ['QEMU PCI testdevs replace GPUs; no NVIDIA firmware executes',
            'Guest BMC maps Redfish POST to real guest-kernel bridge resets',
            'Fixture lifecycle helper models exclusive ownership and fabric receipts',
            'File-backed systemctl model; real production service-hold ledger',
            'Counter checks replace NVML/CUDA and tenant workload execution']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('GPU_QEMU_RESULT=' + json.dumps({'passed': False, 'error': traceback.format_exc()}), flush=True)

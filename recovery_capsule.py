"""Boot-bound pre-fault domain records for independently controlled recovery.

A capsule supplies expected topology, never physical reset authority. A fresh
adapter preflight must additionally establish independent control of missing
functions. No cached BARs or PCI configuration bytes are ever restored.
"""
import dataclasses
import fcntl
import hashlib
import json
import os
import pathlib
import tempfile
import time

FIELDS = ('vendor', 'device', 'subsystem_vendor', 'subsystem_device', 'class')
LOCK_PATH = pathlib.Path('/run/gpu-recovery-orchestrator.lock')
BOARD_FIELDS = ('board_vendor', 'board_name', 'product_name', 'bios_vendor', 'bios_version', 'bios_date')


def identity(g, path):
    return {'path': str(path.resolve()), **{key: g.text(path/key) for key in FIELDS}}


def binding(g, backend):
    return {'boot_id': g.text('/proc/sys/kernel/random/boot_id'),
            'kernel': os.uname().release, 'driver': g.text('/sys/module/nvidia/version'),
            'board': {key: g.text(g.DMI/key) for key in BOARD_FIELDS},
            'backend_sha256': hashlib.sha256(json.dumps(backend, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def location(g, backend):
    value = backend.get('domain', {}).get('capsule')
    if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
        raise g.RecoveryError('Domain capsule must be an absolute root-managed file path')
    return pathlib.Path(value)


def capture(g, cfg, backend, root=None):
    if os.geteuid():
        raise g.RecoveryError('Domain preparation requires root')
    with LOCK_PATH.open('a') as lock:
        g.trusted_file(LOCK_PATH)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _capture(g, cfg, backend, root)


def _capture(g, cfg, backend, root=None):
    """Read identities/topology and write a trusted record; no device mutations."""
    import domain_adapter as domain
    root = g.SYSFS if root is None else root
    if os.geteuid():
        raise g.RecoveryError('Domain preparation requires root')
    approved = domain.members(g, backend)
    if backend.get('prefer_backend') is not True:
        raise g.RecoveryError('Capsule requires an explicit preferred lifecycle adapter')
    expected = cfg.get('expected_gpu_uuids', {})
    if any(expected.get(bdf) != uuid for bdf, uuid in approved.items()):
        raise g.RecoveryError('Capsule preparation requires every domain UUID to be provisioned')
    constraints=backend.get('match')
    if not isinstance(constraints,dict) or not constraints:
        raise g.RecoveryError('Capsule requires explicit platform identity constraints')
    for name, value in constraints.items():
        if name not in ('board_name','board_vendor','product_name') or not isinstance(value,str) or not value.strip() or g.text(g.DMI/name)!=value:
            raise g.RecoveryError('Capsule platform identity mismatch')
    targets, scope, _ = domain.inventory(g, approved, root)
    for bdf, uuid in approved.items():
        if g.nvml_identity(bdf) != uuid:
            raise g.IdentityMismatch('Healthy GPU identity differs from provisioned capsule identity')
    before = {bdf: identity(g, root/bdf) for bdf in scope}
    bridges = {}
    for target in targets.values():
        bridge = target.bridge
        path = g.subordinate_rescan_path(bridge, root)
        if path is None:
            raise g.RecoveryError('Capsule requires a unique subordinate bus rescan interface')
        bridges[bridge] = {'identity': identity(g, root/bridge), 'rescan_path': str(path.resolve())}
    record = {'schema': 1, 'binding': binding(g, backend), 'created_monotonic': time.monotonic(),
              'members': approved, 'scope': scope, 'functions': before, 'bridges': bridges,
              'targets': {bdf: dataclasses.asdict(target) for bdf, target in targets.items()}}
    if not record['binding']['boot_id']:
        raise g.RecoveryError('Boot identity unavailable')
    # Recheck after all driver queries; never install a mixed-time snapshot.
    inspect_scope(g, record, backend, root, complete=True)
    path = location(g, backend)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    g.trusted_file(path.parent)
    if path.exists():
        g.trusted_file(path)
    fd, temporary = tempfile.mkstemp(prefix='.capsule-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'capsule': str(path), 'members': approved, 'scope': scope,
            'device_state_changed': False, 'hardware_recovery_verified': False}


def load(g, backend):
    path = g.trusted_file(location(g, backend))
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise g.RecoveryError('Cannot read domain capsule') from None
    return value


def inspect_scope(g, value, backend, root=None, complete=False):
    import domain_adapter as domain
    root = g.SYSFS if root is None else root
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise g.RecoveryError('Unsupported domain capsule schema')
    approved = domain.members(g, backend)
    if value.get('members') != approved or value.get('binding') != binding(g, backend):
        raise g.RecoveryError('Domain capsule boot, firmware, driver, identity or backend changed')
    age = time.monotonic() - value.get('created_monotonic', -1)
    maximum = backend['domain'].get('capsule_max_age', 86400)
    if type(maximum) not in (int, float) or not 60 <= maximum <= 604800:
        raise g.RecoveryError('capsule_max_age must be 60..604800 seconds')
    if not 0 <= age <= maximum:
        raise g.RecoveryError('Domain capsule expired; prepare it while the whole domain is healthy')
    functions, bridges, targets = value.get('functions'), value.get('bridges'), value.get('targets')
    if not all(isinstance(v, dict) and v for v in (functions, bridges, targets)):
        raise g.RecoveryError('Malformed capsule topology')
    scope = value.get('scope')
    if scope != sorted(functions) or not set(approved) <= set(scope) or set(targets) != set(approved):
        raise g.RecoveryError('Malformed capsule membership or scope')
    if any(not isinstance(bdf, str) or not g.BDF.fullmatch(bdf) for bdf in [*scope, *bridges]):
        raise g.RecoveryError('Malformed capsule PCI address')
    # Every recorded function must belong to a recorded member's slot. Nested
    # bridge/switch resets remain unsupported by the local domain contract.
    if any(not any(bdf[:-1] == member[:-1] for member in approved) for bdf in scope):
        raise g.RecoveryError('Capsule contains an unapproved PCI slot')
    if {item.get('bridge') for item in targets.values() if isinstance(item,dict)} != set(bridges):
        raise g.RecoveryError('Capsule contains an unrelated rescan bridge')
    for recorded in functions.values():
        if (not isinstance(recorded,dict) or recorded.get('vendor')!='0x10de'
                or not (recorded.get('class') or '').startswith(('0x03','0x04','0x0c03','0x0c80','0x1200'))):
            raise g.RecoveryError('Capsule contains a non-GPU or unsupported auxiliary function')
    current = set()
    for bdf, recorded in bridges.items():
        parent = root/bdf
        if not parent.exists() or identity(g, parent) != recorded.get('identity'):
            raise g.RecoveryError('Capsule parent identity or physical path changed')
        if not g.text(parent/'class').startswith('0x0604'):
            raise g.RecoveryError('Capsule parent is not a PCI bridge')
        rescan = g.subordinate_rescan_path(bdf, root)
        if rescan is None or str(rescan.resolve()) != recorded.get('rescan_path'):
            raise g.RecoveryError('Capsule subordinate bus identity changed')
        descendants = {p.name for p in parent.resolve().rglob('*') if g.BDF.fullmatch(p.name)}
        if not descendants <= set(scope):
            raise g.RecoveryError('Unapproved device appeared inside the capsule reset domain')
        current.update(descendants)
    for bdf, recorded in functions.items():
        if (root/bdf).exists():
            if bdf not in current or identity(g, root/bdf) != recorded:
                raise g.RecoveryError('Capsule function identity or topology changed')
        elif bdf in current:
            raise g.RecoveryError('PCI inventory and capsule subtree disagree')
    missing = sorted(set(scope)-current)
    if complete and missing:
        raise g.RecoveryError('Capsule domain did not return completely after recovery')
    restored = {}
    for bdf, item in targets.items():
        if (not isinstance(item, dict) or item.get('bdf') != bdf or item.get('bridge') not in bridges
                or item.get('slot') != sorted(p for p in scope if p[:-1] == bdf[:-1])):
            raise g.RecoveryError('Malformed capsule target topology')
        expected_parent = pathlib.Path(bridges[item['bridge']]['identity']['path'])
        for function in item['slot']:
            if pathlib.Path(functions[function]['path']).parent != expected_parent:
                raise g.RecoveryError('Capsule slot is outside its recorded parent')
        restored[bdf] = g.Target(**item)
    return restored, scope, missing


def rediscover(g, value, backend, root=None):
    """One scoped rescan per recorded child bus, after independent reset only."""
    root = g.SYSFS if root is None else root
    inspect_scope(g, value, backend, root)
    for bdf in sorted(value['bridges']):
        inspect_scope(g, value, backend, root)
        path = g.subordinate_rescan_path(bdf, root)
        if path is None:
            raise g.RecoveryError('Capsule child bus disappeared before rescan')
        g.write_sysfs(path, '1')
    inspect_scope(g, value, backend, root, complete=True)

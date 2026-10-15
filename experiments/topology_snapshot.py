#!/usr/bin/env python3
"""Capture/assess pre-fault topology. Never writes PCI state or authorizes reset."""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_recovery as g

BOOT = pathlib.Path('/proc/sys/kernel/random/boot_id')


def identity(path):
    fields = ('vendor', 'device', 'subsystem_vendor', 'subsystem_device', 'class')
    return {'path': str(path.resolve()), **{key: g.text(path/key) for key in fields}}


def children(bridge, root):
    parent = (root/bridge).resolve()
    return sorted(p.name for p in root.iterdir()
                  if g.BDF.fullmatch(p.name) and parent in p.resolve().parents)


def capture(bdf, root=g.SYSFS, boot_path=BOOT):
    target = g.inspect(bdf, root)
    if not target.isolated:
        raise g.RecoveryError('Snapshot requires an isolated GPU slot')
    boot = g.text(boot_path)
    if not boot:
        raise g.RecoveryError('Boot identity unavailable')
    return {'schema': 1, 'boot_id': boot, 'gpu_bdf': target.bdf,
            'bridge_bdf': target.bridge, 'slot_functions': target.slot,
            'gpu_identity': identity(root/target.bdf),
            'bridge_identity': identity(root/target.bridge),
            'scope': children(target.bridge, root),
            'purpose': 'Diagnostic snapshot only; not reset authorization'}


def assess(snapshot, root=g.SYSFS, boot_path=BOOT):
    reasons = []
    if snapshot.get('schema') != 1:
        raise g.RecoveryError('Unsupported snapshot schema')
    bdf, bridge = snapshot.get('gpu_bdf'), snapshot.get('bridge_bdf')
    if not isinstance(bdf, str) or not isinstance(bridge, str) or not g.BDF.fullmatch(bdf) or not g.BDF.fullmatch(bridge):
        raise g.RecoveryError('Malformed snapshot PCI addresses')
    slot = snapshot.get('slot_functions')
    if not isinstance(slot, list) or not slot or bdf not in slot or any(not isinstance(p, str) or not g.BDF.fullmatch(p) or p[:-1] != bdf[:-1] for p in slot):
        raise g.RecoveryError('Malformed snapshot slot')
    if snapshot.get('scope') != sorted(set(slot)):
        reasons.append('Snapshot did not describe an isolated slot')
    boot = g.text(boot_path)
    if not boot or snapshot.get('boot_id') != boot:
        reasons.append('Boot changed or unavailable')
    parent = root/bridge
    if not parent.exists() or identity(parent) != snapshot.get('bridge_identity'):
        reasons.append('Parent identity or topology changed')
    scope = children(bridge, root) if parent.exists() else []
    if set(scope) - set(slot):
        reasons.append('Unexpected downstream devices')
    present = (root/bdf).exists()
    if present and identity(root/bdf) != snapshot.get('gpu_identity'):
        reasons.append('Target identity or topology changed')
    if present and bdf not in scope:
        reasons.append('Target is no longer under the recorded parent')
    if not present and scope:
        reasons.append('Target missing but downstream scope is not empty')
    rescan = g.subordinate_rescan_path(bridge, root) if not reasons else None
    return {'snapshot_consistent': not reasons, 'reasons': reasons,
            'target_present': present, 'current_scope': scope,
            'subordinate_rescan_path': str(rescan) if rescan else None,
            'reset_authorized': False, 'hardware_recovery_verified': False,
            'limitations': 'Same PCI IDs and path do not prove unique physical identity. No UUID, fabric ownership, independent reset endpoint or driver state is validated.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    cap = sub.add_parser('capture');cap.add_argument('--gpu', required=True)
    check = sub.add_parser('assess');check.add_argument('snapshot', type=pathlib.Path)
    args = parser.parse_args()
    report = capture(args.gpu) if args.action == 'capture' else assess(json.loads(args.snapshot.read_text()))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

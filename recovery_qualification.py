#!/usr/bin/env python3
"""Offline qualification of proposed independent GPU recovery domains.

Declarative engineering evidence only: never authorizes or performs a reset.
"""
import argparse
import json
from pathlib import Path


class InvalidManifest(ValueError):
    pass


def qualify(m):
    if not isinstance(m, dict) or m.get('schema') != 1:
        raise InvalidManifest('Expected a schema-1 object')
    members = m.get('members')
    if (not isinstance(members, list) or not members or
            any(not isinstance(x, str) or not x for x in members) or
            len(set(members)) != len(members)):
        raise InvalidManifest('members must contain unique nonempty identities')
    member_set = set(members)
    blockers = []
    def require(value, message):
        if value is not True:
            blockers.append(message)
    controls = m.get('controls', [])
    if not isinstance(controls, list):
        raise InvalidManifest('controls must be an array')
    usable = []
    seen = set()
    for c in controls:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'] or c['id'] in seen:
            raise InvalidManifest('Each control needs a unique nonempty id')
        seen.add(c['id'])
        scope = c.get('affected_members')
        if not isinstance(scope, list) or not scope or any(not isinstance(x, str) for x in scope):
            raise InvalidManifest('Each control needs affected_members')
        valid_scope = set(scope) <= member_set
        if not valid_scope:
            blockers.append(f"{c['id']}: affects devices outside the approved domain")
        independent = all(c.get(k) is True for k in (
            'transport_survives_gpu_loss', 'controller_power_independent',
            'mapping_physically_verified', 'exclusive_ownership',
            'durable_ambiguous_operation_handling'))
        if not independent:
            blockers.append(f"{c['id']}: independent control/ownership evidence incomplete")
        if valid_scope and independent:
            usable.append(c)
    supplies = m.get('supplies', [])
    if not isinstance(supplies, list):
        raise InvalidManifest('supplies must be an array')
    coverage = {}
    for member in members:
        rails = []
        for s in supplies:
            if not isinstance(s, dict) or s.get('member') not in member_set:
                raise InvalidManifest('Each supply must name an approved member')
            if s['member'] != member:
                continue
            if not isinstance(s.get('id'), str) or not s['id'] or s['id'] in rails:
                raise InvalidManifest('Supply ids must be unique per member')
            rails.append(s['id'])
            matches = [c for c in usable if c.get('kind') == 'power' and
                       member in c['affected_members'] and
                       c['id'] == s.get('disconnect_control')]
            require(bool(matches), f"{member}/{s['id']}: no qualified independent disconnect")
            require(s.get('off_voltage_measured'), f"{member}/{s['id']}: off voltage unverified")
            require(s.get('discharge_time_measured'), f"{member}/{s['id']}: discharge time unverified")
        coverage[member] = rails
        require(bool(rails), f'{member}: no supply inventory')
    evidence = m.get('evidence', {})
    if not isinstance(evidence, dict):
        raise InvalidManifest('evidence must be an object')
    for key in ('all_power_and_backfeed_paths_inventoried', 'sequencing_vendor_approved',
                'driver_teardown_bounded', 'tenant_and_fabric_drain_verified',
                'fresh_identity_and_workload_verified', 'unrelated_devices_preserved',
                'actual_lost_link_recovery_tested', 'repeat_fault_recovery_tested'):
        require(evidence.get(key), f'evidence missing: {key}')
    boot_changed = evidence.get('host_boot_unchanged')
    require(boot_changed, 'evidence missing: host_boot_unchanged')
    return {'schema': 1, 'members': members, 'supply_coverage': coverage,
            'declared_requirements_complete': not blockers,
            'blockers': blockers, 'hardware_verified_by_this_tool': False,
            'reset_authorized': False,
            'scope': 'Independent full-power recovery proposal; not every reset mechanism'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    try:
        result = qualify(json.loads(args.manifest.read_text()))
    except (OSError, ValueError) as error:
        parser.exit(2, f'Invalid manifest: {error}\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['declared_requirements_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

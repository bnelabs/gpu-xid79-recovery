#!/usr/bin/env python3
"""Disposable guest Switchtec command/sideband model, not a vendor emulator."""
import contextlib
import io
import json
import os
import pathlib
import sys

sys.path.insert(0, '/lab')
import gpu_recovery as g
import enterprise_fixture as fixture


def main():
    if 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
        raise RuntimeError('Disposable guest kernel marker required')
    cfg = fixture.state()
    if len(sys.argv) > 1:
        argv = sys.argv[1:]
        if len(argv) != 9 or argv[:3] != ['fabric', 'port_control', 'LAB-SIDEBAND']:
            raise RuntimeError('Only scoped hot-reset CLI is modeled')
        if argv[3] != '--phys_port_id' or argv[5:] not in (
                ['--control_type', 'HOT_RESET', '--hot_reset_flag', 'SET'],
                ['--control_type', 'HOT_RESET', '--hot_reset_flag', 'CLEAR']):
            raise RuntimeError('Invalid modeled command')
        port = argv[4]
        if port not in cfg['ports']:
            raise RuntimeError('Physical port not provisioned')
        flag = argv[-1]
        fixture.event(kind='switch-command', port=port, flag=flag)
        asserted = cfg.setdefault('asserted', [])
        if flag == 'SET':
            if port in asserted:
                raise RuntimeError('Duplicate assertion')
            asserted.append(port)
            fixture.STATE.write_text(json.dumps(cfg))
            if cfg['fault'] == 'set-response-lost':
                raise RuntimeError('Injected response loss after assertion')
        else:
            if cfg['fault'] == 'clear-failed':
                raise RuntimeError('Injected release failure')
            if port not in asserted:
                raise RuntimeError('Port was not asserted')
            parent = pathlib.Path(cfg['ports'][port])
            # Explicit sideband model: real kernel reset happens at release.
            g.write_sysfs(parent/'reset_subordinate', '1')
            asserted.remove(port)
            fixture.STATE.write_text(json.dumps(cfg))
        return
    if os.environ.get('GPU_RECOVERY_PHASE') == 'health':
        if json.loads(os.environ['GPU_DOMAIN_MEMBERS']) != cfg['members']:
            raise RuntimeError('Independent health mapping differs')
        for parent in cfg['ports'].values():
            if not pathlib.Path(parent).exists():
                raise RuntimeError('Independent health lost mapped parent')
        states = {bdf: 'recoverable_fault' for bdf in cfg['members']}
        if cfg['fault'] == 'watch-health-hold':
            states[sorted(states)[-1]] = 'hold'
        value = {'schema': 1, 'nonce': os.environ['GPU_RECOVERY_NONCE'],
            'boot_id': g.text('/proc/sys/kernel/random/boot_id'),
            'members': cfg['members'], 'independent_health': True, 'states': states}
        if cfg['fault'] == 'watch-health-stale':value['nonce'] = 'old-observation'
        fixture.event(kind='independent-health', states=states)
        print(json.dumps(value))
        return
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        fixture.lifecycle()
    value = json.loads(stream.getvalue())
    expected = {bdf: {'uuid': uuid, 'physical_port': int(cfg['gpu_ports'][bdf]),
        'resource': 'switchtec://LAB-PAX/ports/'+cfg['gpu_ports'][bdf]}
        for bdf, uuid in cfg['members'].items()}
    value.update(switchtec_controller_id='LAB-PAX', switchtec_targets=expected,
        independent_reset_control=cfg['fault'] != 'no-independent-control',
        all_partitions_exclusive=True, reset_released=not cfg.get('asserted'),
        switchtec_command_supported=True, transport_survives_gpu_loss=True,
        management_reset_resolved=not cfg.get('asserted'))
    if cfg['fault'] == 'wrong-port-map':
        value['switchtec_targets'] = {}
    print(json.dumps(value))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Explicitly provisioned Switchtec per-port hot-reset domain adapter.

Requires a platform lifecycle helper and qualified independent transport.
No discovery, whole-switch reset, raw register write or automatic reset retry.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_recovery as g
import domain_adapter as domain
from adapters.reset_journal import Journal


class Adapter:
    def __init__(self, cfg):
        self.cfg = cfg
        if type(cfg.get('schema')) is not int or cfg['schema'] != 1:
            raise g.RecoveryError('Switchtec configuration requires schema 1')
        identity = cfg.get('controller_id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
            raise g.RecoveryError('Provision a stable physical controller_id')
        self.origin = 'switchtec://' + identity
        self.device = cfg.get('device')
        if not isinstance(self.device, str) or not self.device or self.device.startswith('-'):
            raise g.RecoveryError('Provision an explicit Switchtec transport device')
        self.command = cfg.get('switchtec_command')
        self.helper = cfg.get('lifecycle_command')
        for command in (self.command, self.helper):
            if (not isinstance(command, list) or not command or
                    not all(isinstance(x, str) and x for x in command) or
                    not pathlib.Path(command[0]).is_absolute()):
                raise g.RecoveryError('Provision absolute executable command arrays')
            g.trusted_file(command[0])
        self.hold = cfg.get('reset_hold_seconds', .1)
        if type(self.hold) not in (int, float) or not .01 <= self.hold <= 1:
            raise g.RecoveryError('Reset hold must be qualified and between .01 and 1 seconds')
        source = cfg.get('targets')
        if not isinstance(source, dict) or not source:
            raise g.RecoveryError('Explicit GPU/physical-port mappings required')
        self.targets = {}
        for bdf, item in source.items():
            if not isinstance(bdf, str) or not g.BDF.fullmatch(bdf) or not isinstance(item, dict):
                raise g.RecoveryError('Invalid GPU mapping')
            if not isinstance(item.get('uuid'), str) or not domain.UUID.fullmatch(item['uuid']):
                raise g.RecoveryError('Provision physical GPU UUID')
            port = item.get('physical_port')
            if type(port) is not int or not 0 <= port <= 255:
                raise g.RecoveryError('Physical port must be a provisioned uint8')
            self.targets[bdf] = {'uuid': item['uuid'], 'physical_port': port,
                                'resource': self.origin + '/ports/' + str(port)}
        if len({t['uuid'] for t in self.targets.values()}) != len(self.targets):
            raise g.RecoveryError('GPU UUIDs must be unique')

    def execute(self, phase, env):
        if phase not in ('preflight', 'recover', 'validation'):
            raise g.RecoveryError('Unknown adapter phase')
        approved = {bdf: t['uuid'] for bdf, t in self.targets.items()}
        try:
            members = json.loads(env['GPU_DOMAIN_MEMBERS'])
            scope = json.loads(env['GPU_DOMAIN_SCOPE'])
            missing = json.loads(env.get('GPU_MISSING_FUNCTIONS', '[]'))
            boot, nonce = env['GPU_RECOVERY_BOOT_ID'], env['GPU_RECOVERY_NONCE']
        except (KeyError, ValueError):
            raise g.RecoveryError('Controller domain environment required') from None
        if (members != approved or not isinstance(scope, list) or
                any(not isinstance(x, str) or not g.BDF.fullmatch(x) for x in scope) or
                len(set(scope)) != len(scope) or not set(approved) <= set(scope)):
            raise g.RecoveryError('Controller domain differs from provisioned GPU mapping')
        if (not isinstance(missing, list) or any(not isinstance(x, str) or x not in scope for x in missing)
                or len(set(missing)) != len(missing)):
            raise g.RecoveryError('Invalid missing-function scope')
        if (boot != g.text('/proc/sys/kernel/random/boot_id') or not isinstance(nonce, str) or not nonce
                or env.get('GPU_BDF') not in approved):
            raise g.RecoveryError('Invalid operation identity')
        intent = env.get('GPU_RECOVERY_INTENT')
        if intent not in ('recover', 'resume') or phase == 'recover' and intent != 'recover':
            raise g.RecoveryError('Explicit controller recovery intent required')
        deadline = time.monotonic() + 45
        def bounded_run(argv, timeout, child_env):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise g.CommandTimeout('Switchtec adapter deadline exceeded; reconcile reset state')
            return g.run(argv, min(timeout, remaining), child_env)
        class Runner:
            RecoveryError = g.RecoveryError
            run = staticmethod(bounded_run)
        with Journal(g, self.cfg.get('state_dir', '/var/lib/gpu-recovery-generic/switchtec'),
                     self.origin, self.targets, scope) as journal:
            journal.check(phase, intent, nonce)
            helper_env = dict(env, GPU_RECOVERY_PHASE='validation' if phase == 'validation' else 'preflight',
                GPU_SWITCHTEC_CONTROLLER_ID=self.cfg['controller_id'],
                GPU_SWITCHTEC_DEVICE=self.device,
                GPU_SWITCHTEC_TARGETS=json.dumps(self.targets, sort_keys=True),
                GPU_PENDING_RESETS=json.dumps(journal.overlaps(), sort_keys=True))
            required = {'switchtec_controller_id': self.cfg['controller_id'],
                'switchtec_targets': self.targets, 'independent_reset_control': True,
                'all_partitions_exclusive': True, 'reset_released': True,
                'switchtec_command_supported': True, 'transport_survives_gpu_loss': True}
            if missing:
                required['missing_functions'] = missing
            value = domain.receipt(Runner, self.helper, helper_env, approved, scope, boot, nonce,
                                   helper_env['GPU_RECOVERY_PHASE'], required)
            if phase == 'recover':
                journal.arm(nonce, boot)
                # A downstream bridge can contain several approved GPUs. Pulse
                # each physical port once, never once per endpoint below it.
                for port in sorted({t['physical_port'] for t in self.targets.values()}):
                    resource = self.origin + '/ports/' + str(port)
                    journal.pending(nonce, resource)  # fsync BEFORE assertion
                    for flag in ('SET', 'CLEAR'):
                        argv = self.command + ['fabric', 'port_control', self.device,
                            '--phys_port_id', str(port), '--control_type', 'HOT_RESET',
                            '--hot_reset_flag', flag]
                        rc, _ = bounded_run(argv, 15, env)
                        if rc:
                            raise g.RecoveryError(f'Switchtec hot-reset {flag} failed; reconcile before retry')
                        if flag == 'SET':
                            time.sleep(self.hold)
                    journal.completed(nonce, resource)
                journal.awaiting_validation(nonce)
                return {'reset_ports_completed': len({t['physical_port'] for t in self.targets.values()}),
                        'workloads_verified': False}
            if phase == 'validation':
                if journal.overlaps() and intent == 'resume' and value.get('management_reset_resolved') is not True:
                    raise g.RecoveryError('Lifecycle helper has not reconciled pending switch reset')
                journal.clear_verified()
            return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    adapter = Adapter(g.config(args.config, privileged=True))
    print(json.dumps(adapter.execute(os.environ.get('GPU_RECOVERY_PHASE'), os.environ), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

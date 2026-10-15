import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import gpu_recovery as g
from adapters.switchtec_gpu import Adapter

BDF = '0000:01:00.0'
UUID = 'GPU-11111111-2222-3333-4444-555555555555'


class SwitchtecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = {'schema': 1, 'controller_id': 'bench-PAX-serial',
            'device': '/dev/switchtec0', 'switchtec_command': ['/fixture/switchtec'],
            'lifecycle_command': ['/fixture/helper'], 'state_dir': self.temp.name,
            'reset_hold_seconds': .01,
            'targets': {BDF: {'uuid': UUID, 'physical_port': 8}}}
        self.env = {'GPU_DOMAIN_MEMBERS': json.dumps({BDF: UUID}),
            'GPU_DOMAIN_SCOPE': json.dumps([BDF]), 'GPU_BDF': BDF,
            'GPU_RECOVERY_BOOT_ID': g.text('/proc/sys/kernel/random/boot_id'),
            'GPU_RECOVERY_NONCE': 'first', 'GPU_RECOVERY_INTENT': 'recover'}
        self.commands = []
        self.deny = None
        self.resolved = True
        self.fail_flag = None
        self.timeout_flag = None
        self.trust = patch.object(g, 'trusted_file', side_effect=lambda p: pathlib.Path(p))
        self.trust.start()
        self.addCleanup(self.trust.stop)
        self.runner = patch.object(g, 'run', side_effect=self.command_run)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def command_run(self, argv, timeout, env):
        self.commands.append(argv)
        if argv[0] == '/fixture/switchtec':
            self.assertEqual(argv[1:3], ['fabric', 'port_control'])
            # Durable record MUST already exist, including during SET.
            records = json.loads((pathlib.Path(self.temp.name)/'operations.json').read_text())['operations']
            self.assertEqual(records[self.env['GPU_RECOVERY_NONCE']]['stage'], 'request_pending')
            if argv[-1] == self.timeout_flag:
                raise g.CommandTimeout('fixture timeout')
            return (1 if argv[-1] == self.fail_flag else 0), ''
        approved = json.loads(env['GPU_DOMAIN_MEMBERS'])
        value = {'schema': 1, 'nonce': env['GPU_RECOVERY_NONCE'],
            'boot_id': env['GPU_RECOVERY_BOOT_ID'], 'members': approved,
            'scope_bdfs': json.loads(env['GPU_DOMAIN_SCOPE']),
            'exclusive_local_control': True, 'host_reboot_required': False,
            'clients_quiesced': True, 'ready': True, 'workloads_verified': True,
            'fabric_verified': True, 'switchtec_controller_id': self.cfg['controller_id'],
            'switchtec_targets': json.loads(env['GPU_SWITCHTEC_TARGETS']),
            'independent_reset_control': True, 'all_partitions_exclusive': True,
            'reset_released': True, 'switchtec_command_supported': True,
            'transport_survives_gpu_loss': True, 'management_reset_resolved': self.resolved,
            'missing_functions': json.loads(env.get('GPU_MISSING_FUNCTIONS', '[]'))}
        if self.deny:
            value[self.deny] = False
        return 0, json.dumps(value)

    def cli(self):
        return [c for c in self.commands if c[0] == '/fixture/switchtec']

    def test_pulse_once_then_validate_clears_durable_intent(self):
        result = Adapter(self.cfg).execute('recover', self.env)
        self.assertEqual(result['reset_ports_completed'], 1)
        self.assertFalse(result['workloads_verified'])
        self.assertEqual([a[-1] for a in self.cli()], ['SET', 'CLEAR'])
        Adapter(self.cfg).execute('validation', self.env)
        self.assertEqual(json.loads((pathlib.Path(self.temp.name)/'operations.json').read_text())['operations'], {})

    def test_preflight_and_resume_never_reset(self):
        Adapter(self.cfg).execute('preflight', self.env)
        self.env['GPU_RECOVERY_INTENT'] = 'resume'
        Adapter(self.cfg).execute('validation', self.env)
        self.assertEqual(self.cli(), [])
        with self.assertRaises(g.RecoveryError):
            Adapter(self.cfg).execute('recover', self.env)

    def test_timeout_or_failure_at_each_edge_blocks_new_process(self):
        for flag, timeout in [('SET', False), ('CLEAR', False), ('SET', True), ('CLEAR', True)]:
            with self.subTest(flag=flag, timeout=timeout), tempfile.TemporaryDirectory() as state:
                self.cfg['state_dir'] = state
                original = self.temp.name
                self.temp.name = state
                try:
                    self.commands.clear()
                    self.fail_flag = flag if not timeout else None
                    self.timeout_flag = flag if timeout else None
                    with self.assertRaises(g.RecoveryError):
                        Adapter(self.cfg).execute('recover', self.env)
                    self.assertEqual([a[-1] for a in self.cli()], ['SET'] if flag == 'SET' else ['SET', 'CLEAR'])
                    count = len(self.commands)
                    with self.assertRaisesRegex(g.RecoveryError, 'Unresolved management reset'):
                        Adapter(self.cfg).execute('preflight', dict(self.env, GPU_RECOVERY_NONCE='second'))
                    self.assertEqual(len(self.commands), count)
                    self.resolved = False
                    resume = dict(self.env, GPU_RECOVERY_NONCE='second', GPU_RECOVERY_INTENT='resume')
                    with self.assertRaisesRegex(g.RecoveryError, 'not reconciled'):
                        Adapter(self.cfg).execute('validation', resume)
                    self.resolved = True
                    Adapter(self.cfg).execute('validation', resume)
                finally:
                    self.temp.name = original

    def test_required_helper_evidence_blocks_before_assertion(self):
        for field in ('independent_reset_control', 'all_partitions_exclusive',
                      'reset_released', 'switchtec_command_supported', 'transport_survives_gpu_loss',
                      'clients_quiesced', 'switchtec_controller_id',
                      'switchtec_targets'):
            self.deny = field
            with self.assertRaises(g.RecoveryError):
                Adapter(self.cfg).execute('recover', self.env)
            self.assertEqual(self.cli(), [])

    def test_multiple_gpus_on_same_port_are_pulsed_once(self):
        other = '0000:02:00.0'
        uuid = 'GPU-22222222-2222-3333-4444-555555555555'
        self.cfg['targets'][other] = {'uuid': uuid, 'physical_port': 8}
        self.env['GPU_DOMAIN_MEMBERS'] = json.dumps({BDF: UUID, other: uuid})
        self.env['GPU_DOMAIN_SCOPE'] = json.dumps([BDF, other])
        Adapter(self.cfg).execute('recover', self.env)
        self.assertEqual(len(self.cli()), 2)

    def test_bad_identity_scope_and_missing_proof_never_reset(self):
        for key, value in [('GPU_RECOVERY_BOOT_ID', 'different'),
                           ('GPU_DOMAIN_SCOPE', '["bad"]'),
                           ('GPU_MISSING_FUNCTIONS', '["0000:03:00.0"]'),
                           ('GPU_DOMAIN_MEMBERS', '{}')]:
            with self.assertRaises(g.RecoveryError):
                Adapter(self.cfg).execute('recover', dict(self.env, **{key: value}))
        self.env['GPU_MISSING_FUNCTIONS'] = json.dumps([BDF])
        self.deny = 'missing_functions'
        with self.assertRaises(g.RecoveryError):
            Adapter(self.cfg).execute('recover', self.env)
        self.assertEqual(self.cli(), [])

    def test_changed_mapping_cannot_bypass_pending_operation(self):
        self.fail_flag = 'SET'
        with self.assertRaises(g.RecoveryError):
            Adapter(self.cfg).execute('recover', self.env)
        self.cfg['targets'][BDF]['uuid'] = 'GPU-22222222-2222-3333-4444-555555555555'
        self.env['GPU_DOMAIN_MEMBERS'] = json.dumps({BDF: self.cfg['targets'][BDF]['uuid']})
        with self.assertRaisesRegex(g.RecoveryError, 'full original mapping'):
            Adapter(self.cfg).execute('preflight', self.env)

    def test_invalid_config(self):
        for field, value in [('controller_id', ''), ('device', '--probe'),
                             ('reset_hold_seconds', True), ('switchtec_command', ['relative'])]:
            with self.assertRaises(g.RecoveryError):
                Adapter(dict(self.cfg, **{field: value}))
        for port in (True, -1, 256, '8'):
            self.cfg['targets'][BDF]['physical_port'] = port
            with self.assertRaises(g.RecoveryError):
                Adapter(self.cfg)

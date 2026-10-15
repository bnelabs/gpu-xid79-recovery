import json
import pathlib
import tempfile
import types
import unittest
from unittest.mock import Mock

import gpu_recovery as g
from adapters.reset_journal import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)/'state'
        self.target = {'0000:01:00.0': {'uuid': 'GPU-A', 'serial': 'serial-A',
            'resource': '/redfish/v1/Systems/Node/Processors/GPU0'}}
        self.scope = ['0000:01:00.0']
        # Exercise real locks and durable files; production ownership checks are
        # covered by controller tests and exercised as root inside QEMU.
        self.controller = types.SimpleNamespace(RecoveryError=g.RecoveryError,
            trusted_file=Mock(side_effect=lambda p: pathlib.Path(p)))

    def journal(self, targets=None, scope=None):
        return Journal(self.controller, self.directory, 'https://bmc.example',
                       targets or self.target, scope or self.scope)

    def test_crash_pending_record_blocks_new_controller_nonce(self):
        with self.journal() as journal:
            journal.arm('first', 'boot-A')
            journal.pending('first', self.target[self.scope[0]]['resource'])
        with self.journal() as restarted:
            with self.assertRaisesRegex(g.RecoveryError, 'Unresolved management reset'):
                restarted.check('preflight', 'recover', 'second')
            restarted.check('preflight', 'resume', 'second')
        self.assertEqual(json.loads((self.directory/'operations.json').read_text())
                         ['operations']['first']['stage'], 'request_pending')

    def test_successful_post_still_blocks_until_whole_domain_validation(self):
        with self.journal() as journal:
            journal.arm('first', 'boot-A')
            journal.completed('first', self.target[self.scope[0]]['resource'])
            journal.awaiting_validation('first')
        with self.journal() as restarted:
            with self.assertRaises(g.RecoveryError):
                restarted.check('preflight', 'recover', 'new')
            restarted.check('validation', 'recover', 'first')
            restarted.clear_verified()
        with self.journal() as restarted:
            restarted.check('preflight', 'recover', 'new')
            self.assertFalse(restarted.overlaps())

    def test_mapping_change_and_subset_cannot_hide_unresolved_target(self):
        second = '0000:02:00.0'
        self.target[second] = {'uuid': 'GPU-B', 'serial': 'serial-B',
            'resource': '/redfish/v1/Systems/Node/Processors/GPU1'}
        self.scope.append(second)
        with self.journal() as journal:
            journal.arm('first', 'boot-A')
        subset = {self.scope[0]: self.target[self.scope[0]]}
        with self.journal(subset, self.scope[:1]) as changed:
            with self.assertRaisesRegex(g.RecoveryError, 'full original mapping'):
                changed.check('preflight', 'resume', 'second')
        mutated = {key: dict(value, serial='changed') for key, value in self.target.items()}
        with self.journal(mutated) as changed:
            with self.assertRaises(g.RecoveryError):
                changed.check('validation', 'resume', 'second')

    def test_parallel_adapter_cannot_enter_same_journal(self):
        with self.journal():
            with self.assertRaises(BlockingIOError):
                with self.journal():
                    self.fail('Second adapter acquired held lock')

    def test_unrelated_gpu_can_be_recovered_without_clearing_other_latch(self):
        with self.journal() as journal:
            journal.arm('first', 'boot-A')
        other = {'0000:03:00.0': {'resource': '/redfish/v1/Systems/Node/Processors/GPU2'}}
        with self.journal(other, ['0000:03:00.0']) as unrelated:
            unrelated.check('preflight', 'recover', 'second')
            unrelated.clear_verified()
        with self.journal() as original:
            self.assertEqual(set(original.overlaps()), {'first'})

    def test_invalid_journal_fails_closed(self):
        self.directory.mkdir()
        (self.directory/'operations.json').write_text('{"schema":1,"operations":{"bad":{}}}')
        with self.assertRaisesRegex(g.RecoveryError, 'Invalid reset operation record'):
            with self.journal():
                self.fail('Malformed record admitted')

    def test_reboot_does_not_discard_unresolved_management_operation(self):
        with self.journal() as journal:
            journal.arm('first', 'previous-boot')
        with self.journal() as journal:
            with self.assertRaises(g.RecoveryError):
                journal.check('recover', 'recover', 'new-boot-nonce')

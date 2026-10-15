import unittest
from unittest.mock import patch
import gpu_recovery as g
from service_assessment import assess, fleet

BDF = '0000:01:00.0'
UUID = 'GPU-11111111-2222-3333-4444-555555555555'


class AssessmentTests(unittest.TestCase):
    def report(self):
        return {'target': {'bdf': BDF}, 'nvidia': {'uuid': UUID},
                'config_space_reachable': False,
                'kernel_fallback_assessment': {'blocked_by_existing_guards': False,
                                               'unknown_nvidia_fields': []}}

    def config(self):
        return {'watch_gpus': [BDF], 'expected_gpu_uuids': {BDF: UUID},
                'services': [{'unit': 'worker.service'}]}

    def test_no_gaps_is_not_hardware_certification(self):
        result = assess(self.report(), self.config())
        self.assertEqual(result['configuration_gaps'], [])
        self.assertFalse(result['hardware_recovery_verified'])
        self.assertEqual(result['routes']['independent_reset_or_power_control'],
                         'not established by discovery')

    def test_identity_drain_and_validation_gaps(self):
        cfg = {'expected_gpu_uuids': {BDF: UUID.replace('11111111', 'aaaaaaaa')},
               'cuda_validation': False}
        result = assess(self.report(), cfg)
        self.assertEqual(len(result['configuration_gaps']), 4)

    def test_unknown_enterprise_state_and_blocked_fallback(self):
        report = self.report()
        report['kernel_fallback_assessment'].update(
            blocked_by_existing_guards=True, unknown_nvidia_fields=['mig_mode'])
        self.assertEqual(len(assess(report, self.config())['configuration_gaps']), 2)
        report['selected_backend'] = {'command': ['/adapter']}
        result = assess(report, self.config())
        self.assertTrue(result['routes']['lifecycle_adapter_configured'])
        self.assertTrue(any('runtime' in gap for gap in result['configuration_gaps']))

    def test_missing_watched_gpu_is_reported_without_probe(self):
        with patch.object(g, 'discover', return_value=[]), patch.object(g, 'explore') as explore:
            result = fleet(g, self.config())
        explore.assert_not_called()
        self.assertFalse(result['gpus'][0]['present'])

    def test_timeout_stops_fleet_without_probe_of_next_gpu(self):
        with patch.object(g, 'discover', return_value=[BDF, '0000:02:00.0']), \
             patch.object(g, 'inspect', side_effect=g.CommandTimeout('blocked')) as inspect:
            with self.assertRaises(g.CommandTimeout):
                fleet(g, {})
        self.assertEqual(inspect.call_count, 1)

    def test_ordinary_failure_retained_and_next_gpu_assessed(self):
        with patch.object(g, 'discover', return_value=[BDF, '0000:02:00.0']), \
             patch.object(g, 'inspect', side_effect=[g.RecoveryError('unavailable'), object()]), \
             patch.object(g, 'explore', return_value=self.report()), \
             patch.object(g, 'recover') as recover, patch.object(g, 'write_sysfs') as write:
            result = fleet(g, {})
        self.assertEqual(result['gpus'][0]['assessment_error'], 'unavailable')
        self.assertEqual(len(result['gpus']), 2)
        recover.assert_not_called()
        write.assert_not_called()

    def test_invalid_target_rejected(self):
        with patch.object(g, 'discover', return_value=[]):
            with self.assertRaises(g.RecoveryError):
                fleet(g, {}, '../config')

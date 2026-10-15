import copy
import itertools
import unittest

from recovery_qualification import InvalidManifest, qualify


def proposal():
    return {'schema': 1, 'members': ['GPU-bench'], 'controls': [{
        'id': 'carrier', 'kind': 'power', 'affected_members': ['GPU-bench'],
        **{k: True for k in ('transport_survives_gpu_loss', 'controller_power_independent',
            'mapping_physically_verified', 'exclusive_ownership',
            'durable_ambiguous_operation_handling')}}],
        'supplies': [{'id': rail, 'member': 'GPU-bench', 'disconnect_control': 'carrier',
                     'off_voltage_measured': True, 'discharge_time_measured': True}
                    for rail in ('slot-12v', 'slot-3v3', 'aux-12v')],
        'evidence': {k: True for k in ('all_power_and_backfeed_paths_inventoried',
            'sequencing_vendor_approved', 'driver_teardown_bounded',
            'tenant_and_fabric_drain_verified', 'fresh_identity_and_workload_verified',
            'unrelated_devices_preserved', 'actual_lost_link_recovery_tested',
            'repeat_fault_recovery_tested', 'host_boot_unchanged')}}


class QualificationTests(unittest.TestCase):
    def test_declarations_never_authorize_reset(self):
        r = qualify(proposal())
        self.assertTrue(r['declared_requirements_complete'])
        self.assertFalse(r['hardware_verified_by_this_tool'])
        self.assertFalse(r['reset_authorized'])

    def test_every_rail_must_be_controlled_and_measured(self):
        for mask in itertools.product((False, True), repeat=9):
            m = proposal()
            for i, supply in enumerate(m['supplies']):
                supply['disconnect_control'] = 'carrier' if mask[3*i] else 'absent'
                supply['off_voltage_measured'] = mask[3*i+1]
                supply['discharge_time_measured'] = mask[3*i+2]
            self.assertEqual(qualify(m)['declared_requirements_complete'], all(mask))

    def test_each_evidence_and_control_dependency_required(self):
        for section in ('evidence', 'control'):
            source = proposal()['evidence'] if section == 'evidence' else {
                k: v for k, v in proposal()['controls'][0].items() if v is True}
            for field in source:
                for value in (False, None, 1, 'true'):
                    m = proposal()
                    target = m['evidence'] if section == 'evidence' else m['controls'][0]
                    target[field] = value
                    self.assertFalse(qualify(m)['declared_requirements_complete'], field)

    def test_shared_host_power_is_outside_domain(self):
        m = proposal()
        m['controls'][0]['affected_members'].append('host-and-storage')
        self.assertFalse(qualify(m)['declared_requirements_complete'])

    def test_no_supply_inventory_and_unmapped_second_gpu(self):
        for m in (dict(proposal(), supplies=[]), dict(proposal(), members=['GPU-bench', 'GPU-other'])):
            self.assertFalse(qualify(m)['declared_requirements_complete'])

    def test_invalid_manifests(self):
        for m in (None, [], {}, dict(proposal(), members=['x', 'x']),
                  dict(proposal(), controls=[{}]), dict(proposal(), supplies=[{}])):
            with self.assertRaises(InvalidManifest):
                qualify(m)

    def test_duplicate_supply_rejected(self):
        m = proposal()
        m['supplies'].append(copy.deepcopy(m['supplies'][0]))
        with self.assertRaises(InvalidManifest):
            qualify(m)

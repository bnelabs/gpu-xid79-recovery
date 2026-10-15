import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import gpu_recovery as g
import recovery_capsule as c

A = '0000:01:00.0'
UUID = 'GPU-11111111-2222-3333-4444-555555555555'


class CapsuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = pathlib.Path(self.temp.name)
        self.root = base/'pci'
        self.root.mkdir()
        self.bridge = base/'devices/0000:00:01.0'
        self.gpu = self.bridge/A
        self.gpu.mkdir(parents=True)
        for path, vendor, klass in ((self.bridge, '0x8086', '0x060400'), (self.gpu, '0x10de', '0x030000')):
            for name, value in {'vendor': vendor, 'class': klass, 'device': '0x1234',
                                'subsystem_vendor': vendor, 'subsystem_device': '0x5678'}.items():
                (path/name).write_text(value)
            (self.root/path.name).symlink_to(path)
        bus = self.bridge/'pci_bus/0000:01'
        bus.mkdir(parents=True)
        (bus/'rescan').write_text('untouched')
        self.backend = {'prefer_backend': True, 'match': {'board_name': 'Fixture'},
            'domain': {'members': {A: UUID}, 'capsule': str(base/'state/domain.json')}}
        self.cfg = {'expected_gpu_uuids': {A: UUID}}
        native = g.text
        self.addCleanup(patch.stopall)
        patch.object(g, 'trusted_file', side_effect=lambda p: pathlib.Path(p)).start()
        patch.object(g, 'text', side_effect=lambda path, *args: 'Fixture' if pathlib.Path(path)==g.DMI/'board_name' else native(path,*args)).start()
        patch.object(g, 'nvml_identity', return_value=UUID).start()
        patch.object(c.os, 'geteuid', return_value=0).start()
        patch.object(c, 'LOCK_PATH', base/'controller.lock').start()
        c.capture(g, self.cfg, self.backend, self.root)
        self.value = c.load(g, self.backend)

    def assess(self, complete=False):
        return c.inspect_scope(g, self.value, self.backend, self.root, complete)

    def remove(self):
        (self.root/A).unlink()
        for path in self.gpu.iterdir():
            path.unlink()
        self.gpu.rmdir()

    def test_missing_member_keeps_only_captured_scope_and_parent(self):
        self.remove()
        targets, scope, missing = self.assess()
        self.assertEqual(scope, [A])
        self.assertEqual(missing, [A])
        self.assertEqual(targets[A].bridge, self.bridge.name)
        self.assertEqual((self.bridge/'pci_bus/0000:01/rescan').read_text(), 'untouched')
        with self.assertRaisesRegex(g.RecoveryError, 'did not return'):
            self.assess(complete=True)

    def test_changed_boot_or_backend_invalidates_capsule(self):
        self.value['binding']['boot_id'] = 'previous-boot'
        with self.assertRaisesRegex(g.RecoveryError, 'backend changed'):
            self.assess()
        self.value = c.load(g, self.backend)
        self.backend['command'] = ['/different/adapter']
        with self.assertRaises(g.RecoveryError):
            self.assess()

    def test_replacement_uuid_is_refused_during_preparation(self):
        with patch.object(g, 'nvml_identity', return_value='GPU-replacement'):
            with self.assertRaises(g.IdentityMismatch):
                c.capture(g, self.cfg, self.backend, self.root)
        self.assertEqual(c.load(g, self.backend), self.value)

    def test_changed_parent_and_new_storage_peer_refuse_rescan(self):
        self.remove()
        peer = self.bridge/'0000:01:01.0'
        peer.mkdir()
        with patch.object(g, 'write_sysfs') as write:
            with self.assertRaisesRegex(g.RecoveryError, 'Unapproved device'):
                c.rediscover(g, self.value, self.backend, self.root)
        write.assert_not_called()
        peer.rmdir()
        (self.bridge/'device').write_text('0xffff')
        with self.assertRaisesRegex(g.RecoveryError, 'parent identity'):
            self.assess()

    def test_expired_snapshot_and_changed_present_identity_refused(self):
        self.value['created_monotonic'] -= 86401
        with self.assertRaisesRegex(g.RecoveryError, 'expired'):
            self.assess()
        self.value = c.load(g, self.backend)
        (self.gpu/'subsystem_device').write_text('0xffff')
        with self.assertRaisesRegex(g.RecoveryError, 'function identity'):
            self.assess()

    def test_fresh_preparation_requires_all_provisioned_identities(self):
        with self.assertRaisesRegex(g.RecoveryError, 'every domain UUID'):
            c.capture(g, {}, self.backend, self.root)

    def test_invalid_cached_bdf_is_never_used_as_sysfs_path(self):
        self.value['bridges']['../../outside'] = self.value['bridges'].pop(self.bridge.name)
        with self.assertRaisesRegex(g.RecoveryError, 'PCI address'):
            self.assess()

    def test_preparation_changes_no_device_state(self):
        with patch.object(g, 'write_sysfs') as write, patch.object(g, 'recover') as recover:
            result = c.capture(g, self.cfg, self.backend, self.root)
        write.assert_not_called()
        recover.assert_not_called()
        self.assertFalse(result['device_state_changed'])

    def test_changed_child_bus_is_refused(self):
        self.value['bridges'][self.bridge.name]['rescan_path'] += '-different'
        with self.assertRaisesRegex(g.RecoveryError, 'subordinate bus'):
            self.assess()


    def test_capsule_cannot_add_an_unrelated_empty_rescan_bridge(self):
        self.value['bridges']['0000:00:02.0'] = copy.deepcopy(self.value['bridges'][self.bridge.name])
        with self.assertRaisesRegex(g.RecoveryError, 'unrelated rescan bridge'):
            self.assess()

    def test_capsule_cannot_reclassify_a_gpu_slot_as_storage(self):
        self.value['functions'][A]['class']='0x010802'
        with self.assertRaisesRegex(g.RecoveryError, 'non-GPU'):
            self.assess()


class CapsuleRoutingTests(unittest.TestCase):
    def test_explicit_domain_does_not_require_nvidia_diagnostic_query(self):
        import domain_adapter as d
        target=g.Target(A,[A],'0000:00:01.0',True,[],{})
        backend={'domain':{'members':{A:UUID}},'prefer_backend':True}
        with patch.object(g.os,'geteuid',return_value=0), patch.object(g,'inspect',return_value=target), \
             patch.object(g,'explore',side_effect=g.CommandTimeout('wedged diagnostics')) as explore, \
             patch.object(d,'execute',return_value=[A]) as execute:
            self.assertEqual(g.recover(target,{'backends':{A:backend}}),[A])
        explore.assert_not_called();execute.assert_called_once()

    def test_healthy_refresh_is_rate_limited_and_invalid_config_does_not_reset(self):
        watcher=object.__new__(g.Watcher)
        watcher.cfg={'backends':{A:{'domain':{'capsule':'/root/domain.json','capsule_max_age':60}}}}
        watcher.capsule_refresh={}
        with patch.object(c,'capture') as capture, patch.object(g,'recover') as recover:
            watcher.refresh_capsule(A);watcher.refresh_capsule(A)
            self.assertEqual(capture.call_count,1)
            watcher.cfg['backends'][A]['domain']['capsule_max_age']=0
            watcher.refresh_capsule(A)
        recover.assert_not_called()

    def test_missing_prepared_gpu_bypasses_nvml_query_and_uses_budgeted_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            cfg={'expected_gpu_uuids':{A:UUID},'backends':{A:{'domain':{
                'members':{A:UUID},'capsule':str(root/'capsule.json')}}}}
            with patch.object(g,'SYSFS',root), patch.object(g,'trusted_file',side_effect=lambda p:pathlib.Path(p)), \
                 patch.object(g,'nvml_identity') as nvml, patch.object(g,'recover_bdf',return_value=[A]) as recover:
                watcher=g.Watcher(cfg,budget_path=root/'budget.json')
                watcher.check(A)
            nvml.assert_not_called();recover.assert_called_once()
            self.assertEqual(len(watcher.attempts[A]),1)
            self.assertFalse(watcher.failed)

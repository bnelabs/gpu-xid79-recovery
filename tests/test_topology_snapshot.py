import importlib.util
import pathlib
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('snapshot',pathlib.Path(__file__).resolve().parents[1]/'experiments/topology_snapshot.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=pathlib.Path(self.temp.name);self.root=base/'pci';self.root.mkdir()
        self.boot=base/'boot';self.boot.write_text('boot-one')
        self.bridge=base/'devices/0000:00:01.0';self.bridge.mkdir(parents=True)
        self.gpu=self.bridge/'0000:01:00.0';self.gpu.mkdir()
        for path,vendor,device,klass in ((self.bridge,'0x8086','0x1234','0x060400'),(self.gpu,'0x10de','0x2204','0x030000')):
            for name,value in (('vendor',vendor),('device',device),('class',klass)):(path/name).write_text(value)
            (self.root/path.name).symlink_to(path)
        bus=self.bridge/'pci_bus/0000:01';bus.mkdir(parents=True);(bus/'rescan').write_text('untouched')
        self.snap=s.capture(self.gpu.name,self.root,self.boot)
    def report(self):return s.assess(self.snap,self.root,self.boot)
    def remove(self):
        (self.root/self.gpu.name).unlink()
        for p in self.gpu.iterdir():p.unlink()
        self.gpu.rmdir()
    def test_present_snapshot(self):
        report=self.report();self.assertTrue(report['snapshot_consistent']);self.assertFalse(report['reset_authorized'])
    def test_missing_empty_scope_preserves_scoped_path_without_writes(self):
        self.remove();report=self.report();self.assertTrue(report['snapshot_consistent'])
        self.assertFalse(report['target_present']);self.assertEqual(pathlib.Path(report['subordinate_rescan_path']).read_text(),'untouched')
    def test_reboot_invalidates(self):
        self.boot.write_text('boot-two');self.assertFalse(self.report()['snapshot_consistent'])
    def test_parent_replacement_invalidates(self):
        (self.bridge/'device').write_text('0x9999');self.assertFalse(self.report()['snapshot_consistent'])
    def test_new_peer_invalidates(self):
        peer=self.bridge/'0000:01:01.0';peer.mkdir();(self.root/peer.name).symlink_to(peer)
        self.assertFalse(self.report()['snapshot_consistent'])
    def test_missing_with_remaining_function_invalidates(self):
        self.remove();peer=self.bridge/'0000:01:00.1';peer.mkdir();(self.root/peer.name).symlink_to(peer)
        self.assertFalse(self.report()['snapshot_consistent'])
    def test_malformed_cached_path_refused(self):
        self.snap['bridge_bdf']='../../tmp'
        with self.assertRaises(s.g.RecoveryError):self.report()

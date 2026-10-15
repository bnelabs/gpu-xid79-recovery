import pathlib
import tempfile
import unittest
import gpu_recovery as g

class SwitchInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=pathlib.Path(self.temp.name);self.root=base/'pci';self.root.mkdir()
        self.classes=base/'switchtec';self.classes.mkdir()
        self.port=base/'devices/0000:00:01.0';self.port.mkdir(parents=True)
        self.gpu=self.port/'0000:01:00.0';self.gpu.mkdir()
        (self.root/self.gpu.name).symlink_to(self.gpu)
        self.target=g.Target(self.gpu.name,[self.gpu.name],self.port.name,True,[],{})
    def add(self,path):
        path.mkdir(parents=True,exist_ok=True)
        item=self.classes/'switchtec0';item.mkdir();(item/'device').symlink_to(path)
        (item/'partition').write_text('0');return item
    def report(self):return g.switch_management_inventory(self.target,self.root,self.classes)
    def test_sibling_management_does_not_prove_mapping(self):
        self.add(self.port/'0000:01:00.1')
        result=self.report()['switchtec_management_functions'][0]
        self.assertFalse(result['depends_on_target_gpu'])
        self.assertEqual(result['shared_pci_ancestors'],[self.port.name])
        self.assertFalse(result['gpu_port_mapping_verified'])
        self.assertFalse(result['transport_survival_verified'])
    def test_target_dependent_transport_is_identified(self):
        self.add(self.gpu/'controller')
        result=self.report()['switchtec_management_functions'][0]
        self.assertTrue(result['depends_on_target_gpu']);self.assertIsNone(result['pci_bdf'])
    def test_unrelated_controller_and_absent_class(self):
        self.assertEqual(self.report()['switchtec_management_functions'],[])
        self.add(self.port.parent/'0000:00:02.0/0000:02:00.0')
        self.assertEqual(self.report()['switchtec_management_functions'][0]['shared_pci_ancestors'],[])

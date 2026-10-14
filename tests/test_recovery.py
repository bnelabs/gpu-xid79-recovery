import json, pathlib, tempfile, unittest
from unittest.mock import patch, mock_open
import gpu_recovery as g

class RecoveryTests(unittest.TestCase):
    def test_low_level_capabilities_decode_flr_and_extended_reset_scope(self):
        data=bytearray(4096);data[:2]=b'\xde\x10';data[0x34]=0x40
        data[0x40]=0x10;data[0x42:0x44]=(2).to_bytes(2,'little')
        data[0x44:0x48]=(1<<28).to_bytes(4,'little')
        data[0x100:0x104]=(0x15|(1<<16)|(0x120<<20)).to_bytes(4,'little')
        data[0x120:0x124]=(0x1d|(1<<16)).to_bytes(4,'little')
        caps=g.pci_capabilities(data)
        self.assertTrue(caps['standard'][0]['flr_advertised'])
        self.assertEqual([c['name'] for c in caps['extended']],['Resizable BAR','DPC'])
    def test_lost_gpu_and_capability_loops_do_not_invent_support(self):
        self.assertEqual(g.pci_capabilities(b'\xff'*4096)['standard'],[])
        data=bytearray(256);data[:2]=b'\xde\x10';data[0x34]=0x40
        data[0x40:0x42]=bytes([5,0x40])
        self.assertEqual(len(g.pci_capabilities(data)['standard']),1)
    def profile_report(self,mode='valid',count=1):
        temp,root=self.fixture()
        self.addCleanup(temp.cleanup)
        base=pathlib.Path(temp.name);dmi=base/'dmi';dmi.mkdir()
        (dmi/'board_name').write_text('Test Board')
        (root/'0000:07:00.0/device').write_text('0x1234')
        profiles=base/'profiles';profiles.mkdir()
        profile={'match':{'board_name':'Wrong Board' if mode=='mismatch' else 'Test Board',
                         'vendor':'0x10de','device':'0x1234'},
                 'backend':{'command':['/verified/adapter'],'requires_single_gpu':True},
                 'check_command':['/verified/adapter','--check']}
        for i in range(count): (profiles/f'{i}.json').write_text(json.dumps(profile))
        with patch.object(g,'DMI',dmi),patch.object(g,'trusted_file'),patch.object(g.os,'geteuid',return_value=0),patch.object(g,'run',return_value=(1 if mode=='failed_check' else 0,'checked')) as run,patch.object(g,'write_sysfs') as write:
            report=g.explore(g.inspect('0000:07:00.0',root),{},root,profiles)
            write.assert_not_called()
            if mode=='mismatch': run.assert_not_called()
            else: self.assertEqual(run.call_args.args[0],['/verified/adapter','--check'])
            return report
    def test_exploration_selects_only_checked_hardware_profile(self):
        report=self.profile_report()
        self.assertEqual(report['selected_backend']['command'],['/verified/adapter'])
        cfg=g.discovered_config(report,{})
        self.assertIn('0000:07:00.0',cfg['backends'])
    def test_mismatched_board_never_executes_profile(self):
        self.assertIsNone(self.profile_report('mismatch')['selected_backend'])
    def test_failed_compatibility_check_never_selects_adapter(self):
        self.assertIsNone(self.profile_report('failed_check')['selected_backend'])
    def test_ambiguous_profiles_refuse_selection(self):
        with self.assertRaises(g.RecoveryError): self.profile_report(count=2)
    def fixture(self,extra=False):
        temp=tempfile.TemporaryDirectory();base=pathlib.Path(temp.name)
        root=base/'bus/pci/devices';root.mkdir(parents=True)
        bridge=base/'physical/0000:00:03.0';bridge.mkdir(parents=True)
        for bdf,klass,vendor in [('0000:07:00.0','0x030000','0x10de'),('0000:07:00.1','0x040300','0x10de')]+([('0000:08:00.0','0x010802','0x1234')] if extra else []):
            device=bridge/bdf;device.mkdir();(device/'vendor').write_text(vendor);(device/'class').write_text(klass)
            (device/'reset_method').write_text('flr bus');(root/bdf).symlink_to(device)
        (root/bridge.name).symlink_to(bridge)
        return temp,root
    def test_discovers_nonhardcoded_address_and_isolation(self):
        temp,root=self.fixture()
        with temp:
            self.assertEqual(g.discover(root),['0000:07:00.0'])
            target=g.inspect('0000:07:00.0',root)
            self.assertTrue(target.isolated);self.assertEqual(target.bridge,'0000:00:03.0')
            self.assertEqual(target.slot,['0000:07:00.0','0000:07:00.1'])
    def test_shared_bridge_with_storage_refuses_mutation(self):
        temp,root=self.fixture(True)
        with temp:
            target=g.inspect('0000:07:00.0',root);self.assertFalse(target.isolated)
            with patch.object(g,'write_sysfs') as write:
                with self.assertRaises(g.RecoveryError):g.detached_reset(target,{},root)
                write.assert_not_called()
    def test_blocked_unbind_stops_without_reset_or_rebind(self):
        target=g.Target('0000:07:00.0',['0000:07:00.0','0000:07:00.1'],'0000:00:03.0',True,['flr'],
                        {'0000:07:00.0':'nvidia','0000:07:00.1':'snd_hda_intel'})
        calls=[]
        def write(path,value):
            calls.append((str(path),value))
            if '07:00.0' in str(path):raise g.CommandTimeout('stuck')
        with patch.object(g,'write_sysfs',side_effect=write):
            with self.assertRaises(g.CommandTimeout):g.detached_reset(target,{})
        self.assertEqual(len(calls),2);self.assertTrue(all(p.endswith('/unbind') for p,v in calls))
    def test_reset_rebinds_original_drivers(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root);target.drivers={'0000:07:00.0':'nvidia','0000:07:00.1':'snd_hda_intel'}
            with patch.object(g,'write_sysfs') as write:g.detached_reset(target,{},root)
            calls=[str(c.args[0]) for c in write.call_args_list]
            self.assertTrue(calls[0].endswith('07:00.1/driver/unbind'))
            self.assertTrue(calls[1].endswith('07:00.0/driver/unbind'))
            self.assertTrue(calls[2].endswith('07:00.0/reset'))
            self.assertTrue(calls[3].endswith('drivers/nvidia/bind'))
            self.assertTrue(calls[4].endswith('drivers/snd_hda_intel/bind'))
    def test_timeout_does_not_wait_forever_or_run_next_stage(self):
        with self.assertRaises(g.CommandTimeout):g.run(['/usr/bin/python3','-c','import time;time.sleep(10)'],.02)
    def test_xid_parser_targets_only_79(self):
        self.assertEqual(g.xid_bdf('NVRM: Xid (PCI:0000:07:00): 79, GPU lost'),'0000:07:00.0')
        self.assertIsNone(g.xid_bdf('NVRM: Xid (PCI:0000:07:00): 62, PMU halted'))
    def test_cuda_validation_is_pinned_to_target_uuid(self):
        target=g.Target('0000:07:00.0',[],None,False,[],{})
        with patch.object(g,'run',side_effect=[(0,'GPU-test, 00000000:07:00.0, Test GPU'),(0,'passed')]) as run,patch.object(g,'trusted_file'):
            g.verify(target,{'validation_command':['/trusted/cuda']})
            self.assertEqual(run.call_args.args[2]['CUDA_VISIBLE_DEVICES'],'GPU-test')
    def test_untrusted_executable_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(g.RecoveryError):g.trusted_file(pathlib.Path(d)/'missing')
    def test_active_cuda_clients_abort_before_any_reset(self):
        target=g.Target('0000:07:00.0',[],None,False,[],{})
        with patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value={'selected_backend':None,'selection':'kernel'}),patch.object(g.os,'geteuid',return_value=0),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'Services'),patch.object(g,'run',return_value=(0,'1234')) as run,patch.object(g,'detached_reset') as reset:
            with self.assertRaises(g.RecoveryError):g.recover(target,{})
            self.assertEqual(run.call_count,1);reset.assert_not_called()
    def test_in_use_reset_refusal_is_not_bypassed(self):
        target=g.Target('0000:07:00.0',[],None,False,[],{})
        with patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value={'selected_backend':None,'selection':'kernel'}),patch.object(g.os,'geteuid',return_value=0),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'Services'),patch.object(g,'run',side_effect=[(0,''),(1,'GPU is in use by another client')]) as run,patch.object(g,'detached_reset') as reset:
            with self.assertRaises(g.RecoveryError):g.recover(target,{})
            self.assertEqual(run.call_count,2);reset.assert_not_called()

if __name__=='__main__':unittest.main()

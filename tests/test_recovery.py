import json, pathlib, tempfile, unittest
from unittest.mock import patch, mock_open
import gpu_recovery as g

class RecoveryTests(unittest.TestCase):
    def test_hotplug_scope_includes_bridge_and_peer_not_only_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory);root=base/'pci';root.mkdir();slots=base/'slots';slots.mkdir()
            bridge=base/'tree'/'0000:01:00.0';bridge.mkdir(parents=True)
            for name in ('0000:02:01.0','0000:02:02.0'):(bridge/name).mkdir()
            for path in (bridge,*bridge.iterdir()):(root/path.name).symlink_to(path)
            slot=slots/'arbitrary-label';slot.mkdir();(slot/'address').write_text('0000:01:00');(slot/'power').write_text('1')
            target=g.Target('0000:02:01.0',['0000:02:01.0'],bridge.name,False,[],{})
            report=g.hotplug_inventory(target,root,slots)
            self.assertFalse(report[0]['target_functions_only'])
            self.assertEqual(report[0]['affected_bdfs'],['0000:01:00.0','0000:02:01.0','0000:02:02.0'])
            (slot/'address').write_text('0000:09:00')
            self.assertEqual(g.hotplug_inventory(target,root,slots),[])

    def test_hotplug_multifunction_slot_scope_does_not_claim_full_power_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory);root=base/'pci';root.mkdir();slots=base/'slots';slots.mkdir()
            for name in ('0000:01:00.0','0000:01:00.1'):(root/name).mkdir()
            slot=slots/'42';slot.mkdir();(slot/'address').write_text('0000:01:00');(slot/'power').write_text('1')
            target=g.Target('0000:01:00.0',['0000:01:00.0','0000:01:00.1'],None,False,[],{})
            report=g.hotplug_inventory(target,root,slots)[0]
            self.assertTrue(report['target_functions_only'])
            self.assertFalse(report['full_gpu_power_removal_verified'])

    def test_shared_acpi_reset_resource_is_found_outside_gpu_namespace(self):
        source=r'''Scope (\_SB.PCI0) {
          PowerResource (RST0, 0, 0) { Method (_RST, 0) { HELP () } Method (HELP, 0) { Return (One) } }
          Device (GPU0) { Name (_PRR, Package () { ^RST0 }) }
          Device (NET0) { Method (_PR3, 0) { Return (Package () { ^RST0 }) } }
        }'''
        objects=g.asl_reset_objects(source,include_helpers=True)
        graph=g.acpi_resource_graph(objects,[r'\_SB.PCI0.GPU0',r'\_SB.PCI0'])
        resource=graph['target_resources'][0]
        self.assertEqual(resource['path'],r'\_SB.PCI0.RST0')
        self.assertEqual(resource['other_static_consumers'],[r'\_SB.PCI0.NET0'])
        self.assertEqual(resource['sharing_status'],'multiple_static_consumers')
        self.assertTrue(resource['reset_method_present'])
        self.assertEqual({item['path'] for item in graph['resource_method_trace']['methods']},
                         {r'\_SB.PCI0.RST0._RST',r'\_SB.PCI0.RST0.HELP'})
    def test_dynamic_and_conflicting_acpi_resources_do_not_create_a_reset_plan(self):
        source=r'''PowerResource (\RST0, 0, 0) { Method (_RST, 0) { Return (One) } }
          Device (\GPU0) { Method (_PRR, 0) { If (FLAG) { Return (Package () { \RST0 }) } Return (Package () {}) } }'''
        objects=g.asl_reset_objects(source,include_helpers=True)
        objects+=g.asl_reset_objects(r'PowerResource (\RST0, 0, 0) { Method (_RST, 0) { Return (Zero) } }',include_helpers=True)
        graph=g.acpi_resource_graph(objects,[r'\GPU0'])
        self.assertTrue(graph['target_resources'][0]['ambiguous_definition'])
        self.assertEqual(graph['resource_method_trace']['methods'],[])
        self.assertEqual(graph['dynamic_relationships'],[r'\GPU0._PRR'])
    def test_lost_config_cannot_claim_an_inband_reset_transport(self):
        report={'config_space_reachable':False,'pci_capabilities':{'standard':[]},
                'nvidia':{'recovery_action':'Reset'},'selected_backend':None}
        assessment=g.reset_assessment(report)
        self.assertEqual(assessment['in_band_reset_transport'],'unreachable')
        self.assertIsNone(assessment['flr_advertised'])
    def test_driver_requested_bus_reset_targets_only_isolated_gpu(self):
        target=g.Target('0000:07:00.0',['0000:07:00.0'],'0000:00:03.0',True,['flr'],{})
        report={'selected_backend':None,'selection':'kernel','nvidia':{'recovery_action':'Bus Reset'}}
        with patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value=report),patch.object(g,'discover',return_value=[target.bdf]),patch.object(g.os,'geteuid',return_value=0),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'Services'),patch.object(g,'verify'),patch.object(g,'run',side_effect=[(0,''),(0,'reset done')]) as run:
            g.recover(target,{})
            self.assertEqual(run.call_args.args[0],['/usr/bin/nvidia-smi','--gpu-reset','bus','-i',target.bdf])
    def test_os_denied_reset_cannot_fall_back_to_raw_pci_reset(self):
        target=g.Target('0000:07:00.0',[],None,False,[],{})
        report={'selected_backend':None,'selection':'kernel'}
        with patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value=report),patch.object(g.os,'geteuid',return_value=0),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'Services'),patch.object(g,'detached_reset') as reset,patch.object(g,'run',side_effect=[(0,''),(255,'GPU access blocked by the operating system')]):
            with self.assertRaises(g.RecoveryError):g.recover(target,{})
            reset.assert_not_called()
    def test_acpi_helper_trace_follows_local_and_parent_methods(self):
        objects=g.asl_reset_objects(r'''Scope (\_SB.PCI0) {
          Method (SHRD, 0) { Return (One) }
          Device (GPU0) {
            Method (_RST, 0) { PRST () }
            Method (PRST, 0) { ^SHRD () }
          }
          Device (NET0) { Method (PRST, 0) { Return (Zero) } }
        }''',include_helpers=True)
        trace=g.trace_acpi_helpers(objects,[r'\_SB.PCI0.GPU0'])
        self.assertEqual({o['path'] for o in trace['methods']},{r'\_SB.PCI0.GPU0._RST',r'\_SB.PCI0.GPU0.PRST',r'\_SB.PCI0.SHRD'})
    def test_static_acpi_reset_scope_and_gpio_property(self):
        source=r'''DefinitionBlock ("", "DSDT", 2, "OEM", "TEST", 1) {
          Scope (\_SB.PCI0) {
            Device (RP01) { Method (_RST, 0) { Store (One, RST0) }
              Device (GPU0) { Name (_DSD, Package () { "reset-gpios", Package () { 1, 0 } }) }
            }
            Device (RP02) { Method (_RST, 0) { Store (Zero, RST1) } }
          }
        }'''
        objects=g.asl_reset_objects(source)
        paths={o['path'] for o in objects}
        self.assertEqual(paths,{r'\_SB.PCI0.RP01._RST',r'\_SB.PCI0.RP01.GPU0._DSD',r'\_SB.PCI0.RP02._RST'})
        self.assertEqual(g.acpi_path(r'\_SB_.PCI0.RP01'),g.acpi_path(r'\_SB.PCI0.RP01'))
    def test_static_acpi_ignores_comments_and_separates_unrelated_devices(self):
        objects=g.asl_reset_objects(r'''Scope (\_SB) {
          // Method (_RST, 0) { Bad () }
          Device (NET0) { Method (_RST, 0) { ResetNetwork () } }
          Device (GPU0) { Method (_PS0, 0) { PowerGpu () } }
        }''')
        self.assertEqual([o['path'] for o in objects],[r'\_SB.NET0._RST',r'\_SB.GPU0._PS0'])
    def test_enterprise_gpu_classes_included_but_switch_network_audio_excluded(self):
        temp,root=self.fixture()
        with temp:
            device=root/'0000:07:00.0'
            for klass in ('0x030000','0x030200','0x120000'):
                (device/'class').write_text(klass)
                self.assertEqual(g.discover(root),['0000:07:00.0'])
            for klass in ('0x068000','0x020000','0x040300'):
                (device/'class').write_text(klass)
                self.assertEqual(g.discover(root),[])
    def test_enterprise_reset_scope_refuses_unsafe_kernel_fallback(self):
        for info,count in (({},2),({'nvlink_present':True},1),({'mig_mode':'Enabled'},1),
                           ({'virtualization_mode':'VGPU'},1)):
            with self.subTest(info=info,count=count),self.assertRaises(g.RecoveryError):
                g.kernel_fallback_allowed({'nvidia':info},count)
        g.kernel_fallback_allowed({'nvidia':{'virtualization_mode':'None'}},1)
    def test_enterprise_inventory_parses_mig_fabric_and_nvlink_without_mutation(self):
        xml='<nvidia_smi_log><gpu><product_name>Compute GPU</product_name><mig_mode><current_mig>Enabled</current_mig></mig_mode><gpu_fabric_info><state>Completed</state></gpu_fabric_info></gpu></nvidia_smi_log>'
        with patch.object(g,'run',side_effect=[(0,xml),(0,'GPU0 GPU1\nGPU0 X NV18\nGPU1 NV18 X')]) as run:
            result=g.nvidia_inventory(g.Target('0000:07:00.0',[],None,False,[],{}))
            self.assertEqual(result['mig_mode'],'Enabled');self.assertTrue(result['nvlink_present'])
            self.assertEqual(result['fabric_state'],'Completed')
            self.assertNotIn('--gpu-reset',str(run.call_args_list))
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
        with patch.object(g,'nvidia_inventory',return_value={}),patch.object(g,'DMI',dmi),patch.object(g,'trusted_file'),patch.object(g.os,'geteuid',return_value=0),patch.object(g,'run',return_value=(1 if mode=='failed_check' else 0,'checked')) as run,patch.object(g,'write_sysfs') as write:
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
    def test_bus_mode_resets_bridge_and_restores_slot_drivers(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root)
            target.drivers={target.bdf:'nvidia','0000:07:00.1':'snd_hda_intel'}
            (root/target.bridge/'reset_subordinate').touch()
            with patch.object(g,'write_sysfs') as write:
                g.detached_reset(target,{'kernel_reset_mode':'bus'},root)
            paths=[str(c.args[0]) for c in write.call_args_list]
            self.assertEqual(paths[2],str(root/target.bridge/'reset_subordinate'))
            self.assertFalse(any(p.endswith('/reset') for p in paths))
            self.assertTrue(paths[3].endswith('/drivers/nvidia/bind'))
            self.assertTrue(paths[4].endswith('/drivers/snd_hda_intel/bind'))
    def test_acpi_reset_uses_only_kernel_listed_method_and_restores_order(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root)
            target.reset_methods=['flr','acpi'];target.drivers={target.bdf:'nvidia'}
            with patch.object(g,'write_sysfs') as write:
                g.detached_reset(target,{'kernel_reset_mode':'acpi'},root)
            calls=[(str(c.args[0]),c.args[1]) for c in write.call_args_list]
            self.assertTrue(calls[0][0].endswith('/driver/unbind'))
            self.assertEqual(calls[1],(str(root/target.bdf/'reset_method'),'acpi'))
            self.assertEqual(calls[2],(str(root/target.bdf/'reset'),'1'))
            self.assertEqual(calls[3],(str(root/target.bdf/'reset_method'),'flr acpi'))
            self.assertTrue(calls[4][0].endswith('/drivers/nvidia/bind'))
            target.drivers={}
            with patch.object(g,'write_sysfs') as write:g.detached_reset(target,{'kernel_reset_mode':'acpi'},root)
            self.assertEqual(write.call_args.args,(root/target.bdf/'reset_method','flr acpi'))
    def test_acpi_reset_timeout_stops_before_restoring_methods_or_rebinding(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root);target.reset_methods=['acpi','flr']
            target.drivers={target.bdf:'nvidia'}
            def write(path,value):
                if path.name=='reset':raise g.CommandTimeout('blocked')
            with patch.object(g,'write_sysfs',side_effect=write) as calls:
                with self.assertRaises(g.CommandTimeout):g.detached_reset(target,{'kernel_reset_mode':'acpi'},root)
            self.assertEqual(len(calls.call_args_list),3)
    def test_failed_acpi_reset_restores_method_order_and_original_driver(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root);target.reset_methods=['flr','acpi']
            target.drivers={target.bdf:'nvidia'}
            def write(path,value):
                if path.name=='reset':raise g.RecoveryError('firmware reset failed')
            with patch.object(g,'write_sysfs',side_effect=write) as calls:
                with self.assertRaisesRegex(g.RecoveryError,'firmware reset failed'):
                    g.detached_reset(target,{'kernel_reset_mode':'acpi'},root)
            self.assertEqual(calls.call_args_list[-2].args,(root/target.bdf/'reset_method','flr acpi'))
            self.assertTrue(str(calls.call_args.args[0]).endswith('/drivers/nvidia/bind'))
    def test_lost_link_auto_selects_upstream_reset_without_model_names(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root)
            bus=root/target.bridge/'reset_subordinate';bus.touch()
            for vendor in (b'\xff\xff',b'\x00\x00'):
                (root/target.bdf/'config').write_bytes(vendor)
                self.assertEqual(g.kernel_reset_paths(target,{},root),[bus])
            (root/target.bdf/'config').write_bytes(b'\xde\x10')
            self.assertEqual(g.kernel_reset_paths(target,{},root),[root/target.bdf/'reset',bus])
    def test_unavailable_or_invalid_reset_mode_stops_before_detach(self):
        temp,root=self.fixture()
        with temp:
            target=g.inspect('0000:07:00.0',root)
            for mode in ('bus','acpi','unknown'):
                with patch.object(g,'write_sysfs') as write:
                    with self.assertRaises(g.RecoveryError):g.detached_reset(target,{'kernel_reset_mode':mode},root)
                    write.assert_not_called()
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

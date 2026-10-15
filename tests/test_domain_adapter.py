import contextlib
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch
import gpu_recovery as g
import domain_adapter as d

A='0000:01:00.0';B='0000:02:00.0'
UA='GPU-11111111-2222-3333-4444-555555555555'
UB='GPU-66666666-7777-8888-9999-aaaaaaaaaaaa'
NATIVE_RUN=g.run

class DomainTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  base=pathlib.Path(self.temp.name);self.root=base/'pci';self.root.mkdir()
  for i,bdf in enumerate((A,B),1):
   parent=base/'devices'/f'0000:00:0{i}.0';parent.mkdir(parents=True)
   dev=parent/bdf;dev.mkdir();(self.root/bdf).symlink_to(dev)
   for key,value in {'vendor':'0x10de','device':'0x1234','class':'0x030000'}.items():(dev/key).write_text(value)
  self.approved={A:UA,B:UB}
  self.backend={'command':['/trusted/recover'],'prefer_backend':True,'match':{'board_name':'Fixture'},
                'domain':{'members':self.approved,'preflight_command':['/trusted/preflight'],'validation_command':['/trusted/validate']}}
  self.cfg={'gpu_services':{A:[{'unit':'worker-a.service'}],B:[{'unit':'worker-b.service'}]}}
  self.events=[];self.receipt_mutation=None;self.reset_rc=0;self.bad_gpu=None
  stack=contextlib.ExitStack();self.addCleanup(stack.close)
  stack.enter_context(patch.object(g,'trusted_file'))
  stack.enter_context(patch.object(d.fcntl,'flock'))
  # This file is the only direct open in execute; inventory reads use pathlib.
  stack.enter_context(patch.object(d,'open',create=True,return_value=Mock(__enter__=Mock(),__exit__=Mock(return_value=False))))
  native_text=g.text
  stack.enter_context(patch.object(g,'text',side_effect=lambda path,*args: 'Fixture' if pathlib.Path(path)==g.DMI/'board_name' else native_text(path,*args)))
  stack.enter_context(patch.object(g,'run',side_effect=self.run_command))
  def service(items,bdf):
   obj=Mock();obj.stop.side_effect=lambda:self.events.append(('stop',bdf));obj.restore.side_effect=lambda:self.events.append(('restore',bdf));return obj
  stack.enter_context(patch.object(g,'Services',side_effect=service))
  def verify(target,cfg):
   self.events.append(('verify',target.bdf))
   self.assertEqual(cfg['expected_gpu_uuids'],self.approved)
   if target.bdf==self.bad_gpu:raise g.RecoveryError('GPU workload failed')
  stack.enter_context(patch.object(g,'verify',side_effect=verify))
 def run_command(self,argv,timeout,env):
  phase=env['GPU_RECOVERY_PHASE'];self.events.append((phase,None))
  self.assertIn(env['GPU_RECOVERY_INTENT'], ('recover','resume'))
  if phase=='recover':return self.reset_rc,'adapter outcome'
  value={'schema':1,'nonce':env['GPU_RECOVERY_NONCE'],'boot_id':env['GPU_RECOVERY_BOOT_ID'],
         'members':json.loads(env['GPU_DOMAIN_MEMBERS']),'scope_bdfs':json.loads(env['GPU_DOMAIN_SCOPE']),
         'exclusive_local_control':True,'host_reboot_required':False,'clients_quiesced':True,'ready':True}
  if phase=='validation':value.update(workloads_verified=True,fabric_verified=True)
  if self.receipt_mutation:self.receipt_mutation(value,phase)
  return 0,json.dumps(value)
 def execute(self,reset=True):return d.execute(g,g.inspect(A,self.root),self.cfg,self.backend,reset,self.root)
 def assert_no_restore(self):self.assertFalse(any(event[0]=='restore' for event in self.events))
 def subprocess_backend(self,stale=False):
  code="""import os,json
phase=os.environ['GPU_RECOVERY_PHASE']
v={'schema':1,'nonce':os.environ['GPU_RECOVERY_NONCE'],'boot_id':os.environ['GPU_RECOVERY_BOOT_ID'],
'members':json.loads(os.environ['GPU_DOMAIN_MEMBERS']),'scope_bdfs':json.loads(os.environ['GPU_DOMAIN_SCOPE']),
'exclusive_local_control':True,'host_reboot_required':False,'clients_quiesced':True,'ready':True,
'workloads_verified':True,'fabric_verified':True}
"""
  if stale:code+="if phase=='validation':v['nonce']='stale'\n"
  code+='print(json.dumps(v))'
  command=['/usr/bin/python3','-c',code]
  self.backend['command']=command
  self.backend['domain'].update(preflight_command=command,validation_command=command)
 def test_real_adapter_subprocess_protocol_success_with_synthetic_hardware(self):
  self.subprocess_backend()
  with patch.object(g,'run',side_effect=NATIVE_RUN):self.assertEqual(self.execute(),[A,B])
  self.assertEqual([event for event in self.events if event[0]=='restore'],[('restore',B),('restore',A)])
 def test_real_adapter_subprocess_stale_validation_holds_services(self):
  self.subprocess_backend(stale=True)
  with patch.object(g,'run',side_effect=NATIVE_RUN):
   with self.assertRaisesRegex(g.RecoveryError,'nonce'):self.execute()
  self.assert_no_restore()
 def test_whole_domain_verified_before_any_service_resumes(self):
  self.assertEqual(self.execute(),[A,B])
  self.assertEqual(self.events,[('stop',A),('stop',B),('preflight',None),('recover',None),('verify',A),('verify',B),('validation',None),('restore',B),('restore',A)])
 def test_resume_checks_domain_without_reset(self):
  self.execute(False);self.assertNotIn(('recover',None),self.events);self.assertIn(('validation',None),self.events)
 def test_stale_nonce_stops_before_reset(self):
  self.receipt_mutation=lambda value,phase:value.update(nonce='stale')
  with self.assertRaisesRegex(g.RecoveryError,'nonce'):self.execute()
  self.assertNotIn(('recover',None),self.events);self.assert_no_restore()
 def test_incomplete_members_stops_before_reset(self):
  self.receipt_mutation=lambda value,phase:value.update(members={A:UA})
  with self.assertRaisesRegex(g.RecoveryError,'members'):self.execute()
  self.assertNotIn(('recover',None),self.events)
 def test_scope_expansion_stops_before_reset(self):
  self.receipt_mutation=lambda value,phase:value['scope_bdfs'].append('0000:03:00.0')
  with self.assertRaisesRegex(g.RecoveryError,'scope_bdfs'):self.execute()
  self.assertNotIn(('recover',None),self.events)
 def test_boolean_strings_are_not_authority(self):
  self.receipt_mutation=lambda value,phase:value.update(exclusive_local_control='true')
  with self.assertRaisesRegex(g.RecoveryError,'exclusive_local_control'):self.execute()
 def test_host_reboot_requirement_refused(self):
  self.receipt_mutation=lambda value,phase:value.update(host_reboot_required=True)
  with self.assertRaisesRegex(g.RecoveryError,'host_reboot_required'):self.execute()
  self.assertNotIn(('recover',None),self.events)
 def test_peer_workload_failure_holds_all_services(self):
  self.bad_gpu=B
  with self.assertRaisesRegex(g.RecoveryError,'workload'):self.execute()
  self.assert_no_restore();self.assertNotIn(('validation',None),self.events)
 def test_fabric_failure_holds_all_services(self):
  self.receipt_mutation=lambda value,phase:value.update(fabric_verified=False) if phase=='validation' else None
  with self.assertRaisesRegex(g.RecoveryError,'fabric_verified'):self.execute()
  self.assert_no_restore()
 def test_failed_adapter_never_attempts_kernel_fallback(self):
  self.reset_rc=1
  with patch.object(g,'detached_reset') as fallback:
   with self.assertRaisesRegex(g.RecoveryError,'no fallback'):self.execute()
   fallback.assert_not_called();self.assert_no_restore()
 def test_shared_storage_refused_before_services_or_adapter(self):
  parent=(self.root/A).resolve().parent;peer=parent/'0000:01:01.0';peer.mkdir()
  (self.root/peer.name).symlink_to(peer)
  with self.assertRaisesRegex(g.RecoveryError,'unapproved'):self.execute()
  self.assertEqual(self.events,[])
 def test_conflicting_provisioned_uuid_refused(self):
  self.cfg['expected_gpu_uuids']={A:UB}
  with self.assertRaisesRegex(g.RecoveryError,'contradicts'):self.execute()
  self.assertEqual(self.events,[])
 def test_changed_topology_after_preflight_refused(self):
  def mutate(value,phase):
   if phase=='preflight':((self.root/B).resolve()/'device').write_text('0x9999')
  self.receipt_mutation=mutate
  with self.assertRaisesRegex(g.RecoveryError,'topology changed'):self.execute()
  self.assertNotIn(('recover',None),self.events)
 def test_duplicate_gpu_uuid_refused(self):
  self.approved[B]=UA
  with self.assertRaisesRegex(g.RecoveryError,'Duplicate'):self.execute()
 def test_platform_mismatch_refused_without_action(self):
  self.backend['match']={'board_name':'Wrong'}
  with self.assertRaisesRegex(g.RecoveryError,'platform identity'):self.execute()
  self.assertEqual(self.events,[])
 def test_blank_platform_identity_is_not_a_binding(self):
  self.backend['match']={'product_name':''}
  with self.assertRaisesRegex(g.RecoveryError,'platform identity'):self.execute()
  self.assertEqual(self.events,[])
 def test_storage_in_same_slot_is_not_implicitly_approved(self):
  peer=(self.root/A).resolve().parent/'0000:01:00.1';peer.mkdir()
  (peer/'vendor').write_text('0x144d');(peer/'class').write_text('0x010800')
  (self.root/peer.name).symlink_to(peer)
  with self.assertRaisesRegex(g.RecoveryError,'unsupported auxiliary'):self.execute()
  self.assertEqual(self.events,[])
 def test_preflight_timeout_does_not_invoke_adapter_or_restore(self):
  with patch.object(g,'run',side_effect=g.CommandTimeout('blocked')):
   with self.assertRaises(g.CommandTimeout):self.execute()
  self.assert_no_restore();self.assertNotIn(('recover',None),self.events)

 def prepare_capsule(self):
  import recovery_capsule as c
  for index,bdf in enumerate(self.approved,1):
   parent=(self.root/bdf).resolve().parent
   for key,value in {'vendor':'0x8086','device':'0x1234','class':'0x060400'}.items():(parent/key).write_text(value)
   (self.root/parent.name).symlink_to(parent)
   bus=parent/'pci_bus'/f'0000:0{index}';bus.mkdir(parents=True);(bus/'rescan').write_text('untouched')
  self.backend['domain']['capsule']=str(self.root.parent/'state/domain.json')
  self.cfg['expected_gpu_uuids']=dict(self.approved)
  with patch.object(c.os,'geteuid',return_value=0), patch.object(c,'LOCK_PATH',self.root.parent/'capsule.lock'), \
       patch.object(g,'trusted_file',side_effect=lambda p:pathlib.Path(p)), patch.object(g,'nvml_identity',side_effect=self.approved.get):
   c.capture(g,self.cfg,self.backend,self.root)
  self.backup={p.name:p.read_text() for p in (self.root/A).iterdir() if p.is_file()}
  self.physical=(self.root/A).resolve()
  (self.root/A).unlink();shutil.rmtree(self.physical)
 def execute_absent(self):
  with patch.object(g,'trusted_file',side_effect=lambda p:pathlib.Path(p)):
   return d.execute(g,g.Target(A,[],None,False,[],{}),self.cfg,self.backend,root=self.root)
 def test_missing_capsule_member_needs_independent_control_receipt(self):
  self.prepare_capsule()
  with patch.object(g,'write_sysfs') as write:
   with self.assertRaisesRegex(g.RecoveryError,'missing_functions'):self.execute_absent()
  write.assert_not_called();self.assert_no_restore();self.assertNotIn(('recover',None),self.events)
 def test_missing_member_rescan_and_full_validation_before_release(self):
  self.prepare_capsule()
  self.receipt_mutation=lambda value,phase:value.update(missing_functions=[A],independent_reset_control=True)
  def rescan(path,value):
   self.assertTrue(str(path).endswith('/rescan'));self.assertIn('/pci_bus/',str(path))
   if not self.physical.exists():
    self.physical.mkdir()
    for key,content in self.backup.items():(self.physical/key).write_text(content)
    (self.root/A).symlink_to(self.physical)
  with patch.object(g,'write_sysfs',side_effect=rescan) as write:
   self.assertEqual(self.execute_absent(),[A,B])
  self.assertEqual(write.call_count,2)
  self.assertEqual([event for event in self.events if event[0]=='verify'],[('verify',A),('verify',B)])
  self.assertEqual(self.events[-2:],[('restore',B),('restore',A)])
 def test_new_peer_after_missing_preflight_stops_before_reset_or_rescan(self):
  self.prepare_capsule()
  def mutate(value,phase):
   value.update(missing_functions=[A],independent_reset_control=True)
   if phase=='preflight':(self.physical.parent/'0000:01:01.0').mkdir()
  self.receipt_mutation=mutate
  with patch.object(g,'write_sysfs') as write:
   with self.assertRaisesRegex(g.RecoveryError,'Unapproved device'):self.execute_absent()
  write.assert_not_called();self.assertNotIn(('recover',None),self.events);self.assert_no_restore()
 def test_missing_capsule_resume_never_rescans_or_calls_adapter(self):
  self.prepare_capsule()
  with patch.object(g,'trusted_file',side_effect=lambda p:pathlib.Path(p)), patch.object(g,'write_sysfs') as write:
   with self.assertRaisesRegex(g.RecoveryError,'Resume requires'):
    d.execute(g,g.Target(A,[],None,False,[],{}),self.cfg,self.backend,False,self.root)
  write.assert_not_called();self.assertNotIn(('recover',None),self.events)

 def test_missing_member_rejects_string_independent_control_claim(self):
  self.prepare_capsule()
  self.receipt_mutation=lambda value,phase:value.update(missing_functions=[A],independent_reset_control='true')
  with patch.object(g,'write_sysfs') as write:
   with self.assertRaisesRegex(g.RecoveryError,'independent_reset_control'):self.execute_absent()
  write.assert_not_called();self.assert_no_restore()

 def test_unreadable_capsule_keeps_configured_services_held(self):
  self.prepare_capsule()
  pathlib.Path(self.backend['domain']['capsule']).unlink()
  with patch.object(g,'write_sysfs') as write:
   with self.assertRaisesRegex(g.RecoveryError,'Cannot read domain capsule'):self.execute_absent()
  write.assert_not_called();self.assert_no_restore()
  self.assertIn(('stop',A),self.events);self.assertIn(('stop',B),self.events)
  self.assertNotIn(('recover',None),self.events)

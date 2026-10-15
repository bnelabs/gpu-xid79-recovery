import contextlib
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch,mock_open
import gpu_recovery as g

A='0000:01:00.0';B='0000:02:00.0'
UUID='GPU-11111111-2222-3333-4444-555555555555'

class LedgerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=pathlib.Path(self.temp.name)/'services.json'
  self.trust=patch.object(g,'trusted_file',side_effect=lambda p:p);self.trust.start();self.addCleanup(self.trust.stop)
  self.item={'unit':'inference.service'}
 def service(self,bdf):return g.Services([self.item],bdf,self.path)
 def test_failure_hold_survives_new_controller_with_inactive_service(self):
  with patch.object(g,'run',side_effect=[(0,'active'),(0,''),(3,'inactive'),(0,''),(0,'')]) as run:
   self.service(A).stop();self.service(A).stop();self.service(A).restore()
   self.assertEqual(run.call_args.args[0],['/usr/bin/systemctl','start','inference.service'])
   self.assertEqual(json.loads(self.path.read_text())['items'],{})
 def test_shared_service_waits_until_both_gpus_are_verified(self):
  with patch.object(g,'run',side_effect=[(0,'active'),(0,''),(3,'inactive'),(0,''),(0,'')]) as run:
   self.service(A).stop();self.service(B).stop();self.service(A).restore()
   self.assertEqual(run.call_count,4)
   record=next(iter(json.loads(self.path.read_text())['items'].values()))
   self.assertEqual(record['blocked_by'],[B])
   self.service(B).restore();self.assertEqual(run.call_count,5)
 def test_failed_start_retains_hold_for_later_resume(self):
  with patch.object(g,'run',side_effect=[(0,'active'),(0,''),(1,'start failed')]):
   self.service(A).stop()
   with self.assertRaises(g.RecoveryError):self.service(A).restore()
   self.assertEqual(next(iter(json.loads(self.path.read_text())['items'].values()))['blocked_by'],[A])
 def test_activating_service_is_held_but_inactive_unwanted_service_is_not_started(self):
  with patch.object(g,'run',side_effect=[(3,'activating'),(0,'')]) as run:
   self.service(A).stop();self.assertEqual(run.call_args.args[0][1],'stop')
  other=g.Services([{'unit':'unused.service'}],B,self.path)
  with patch.object(g,'run',return_value=(3,'inactive')) as run:
   other.stop();other.restore();self.assertEqual(run.call_count,1)
 def test_service_mapping_merges_global_and_per_gpu_without_duplicates(self):
  cfg={'services':[self.item],'gpu_services':{A:[self.item,{'unit':'worker-a.service'}]}}
  self.assertEqual(len(g.service_items(cfg,A)),2);self.assertEqual(g.service_items(cfg,B),[self.item])
 def test_untrusted_state_directory_is_refused(self):
  with patch.object(g,'trusted_file',side_effect=g.RecoveryError('untrusted')),patch.object(g,'run') as run:
   with self.assertRaises(g.RecoveryError):self.service(A).stop()
   run.assert_not_called()

class RecoveryRestoreTests(unittest.TestCase):
 def test_resume_keeps_profile_specific_validation(self):
  target=g.Target(A,[],None,False,[],{})
  report={'selected_backend':{'command':['/trusted/backend']},'target':{'bdf':A},'validation_command':['/trusted/fabric-check']}
  with patch.object(g.os,'geteuid',return_value=0),patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value=report),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'verify') as verify,patch.object(g,'Services') as services:
   g.resume(A,{})
   self.assertEqual(verify.call_args.args[1]['validation_command'],['/trusted/fabric-check'])
   services.return_value.restore.assert_called_once()
 def test_failed_workload_does_not_restore_services(self):
  target=g.Target(A,[],None,False,[],{})
  with patch.object(g,'inspect',return_value=target),patch.object(g,'explore',return_value={'selected_backend':None,'selection':'NVML'}),patch.object(g.os,'geteuid',return_value=0),patch('builtins.open',mock_open()),patch.object(g.fcntl,'flock'),patch.object(g,'Services') as services,patch.object(g,'run',side_effect=[(0,''),(0,'reset')]),patch.object(g,'verify',side_effect=g.RecoveryError('CUDA failed')):
   with self.assertRaises(g.RecoveryError):g.recover(target,{'services':[{'unit':'worker.service'}]})
   services.return_value.stop.assert_called_once();services.return_value.restore.assert_not_called()
 def test_expected_uuid_mismatch_refuses_workload_probe(self):
  target=g.Target(A,[],None,False,[],{})
  with patch.object(g,'run',return_value=(0,'GPU-other, 00000000:01:00.0, GPU')) as run:
   with self.assertRaisesRegex(g.RecoveryError,'UUID changed'):g.verify(target,{'expected_gpu_uuids':{A:UUID}})
   self.assertEqual(run.call_count,1)

class WatcherTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=pathlib.Path(self.temp.name)/'budget.json'
  self.trust=patch.object(g,'trusted_file',side_effect=lambda p:p);self.trust.start();self.addCleanup(self.trust.stop)
 def controller(self,cfg=None):return g.Watcher(cfg or {},self.path)
 def test_journal_exit_does_not_leave_watcher_waiting_on_child(self):
  journal=Mock();journal.stdout=[];journal.poll.return_value=None
  # A real empty pipe reaches EOF; use an object with the same iterator/close interface.
  import io
  journal.stdout=io.StringIO('')
  with patch.object(g.os,'geteuid',return_value=0),patch.object(g,'Watcher'),patch.object(g.subprocess,'Popen',return_value=journal):
   with self.assertRaisesRegex(g.RecoveryError,'journal stream ended'):g.watch({'watch_gpus':[A]})
   journal.terminate.assert_called_once();journal.wait.assert_called_once_with(timeout=5)
 def test_learned_uuid_survives_same_boot_restart(self):
  with patch.object(g,'nvml_identity',return_value=UUID):
   self.controller().check(A)
  self.assertEqual(self.controller().identities[A],UUID)
 def test_malformed_health_identity_is_held_without_reset(self):
  with patch.object(g,'nvml_identity',side_effect=g.IdentityMismatch('wrong BDF')),patch.object(g,'Services'),patch.object(g,'recover') as recover:
   c=self.controller();c.check(A);recover.assert_not_called();self.assertIn(A,c.failed)
 def test_healthy_poll_and_old_event_do_not_reset(self):
  with patch.object(g,'nvml_identity',return_value=UUID),patch.object(g,'recover') as recover:
   c=self.controller();c.check(A);c.last_end[A]=100;c.check(A,True,90);recover.assert_not_called()
 def test_uuid_swap_holds_and_refuses_reset(self):
  with patch.object(g,'nvml_identity',return_value='GPU-other'),patch.object(g,'Services') as services,patch.object(g,'recover') as recover:
   c=self.controller({'expected_gpu_uuids':{A:UUID}});c.check(A)
   services.return_value.stop.assert_called_once();recover.assert_not_called();self.assertIn(A,c.failed)
 def test_timeout_holds_without_reset(self):
  with patch.object(g,'nvml_identity',side_effect=g.CommandTimeout('blocked')),patch.object(g,'Services') as services,patch.object(g,'recover') as recover:
   c=self.controller();c.check(A);services.return_value.stop.assert_called_once();recover.assert_not_called()
 def test_attempt_budget_survives_watcher_restart(self):
  with patch.object(g,'nvml_identity',side_effect=g.RecoveryError('offline')),patch.object(g,'Services'),patch.object(g,'inspect',return_value=Mock()),patch.object(g,'recover') as recover:
   c=self.controller()
   for _ in range(3):c.check(A)
   self.assertEqual(recover.call_count,3)
   self.controller().check(A);self.assertEqual(recover.call_count,3)
 def test_failed_gpu_does_not_block_independent_gpu_budget(self):
  with patch.object(g,'nvml_identity',side_effect=g.RecoveryError('offline')),patch.object(g,'Services'),patch.object(g,'inspect',return_value=Mock()),patch.object(g,'recover',side_effect=[g.RecoveryError('failed'),None]) as recover:
   c=self.controller();c.check(A);c.check(A);c.check(B)
   self.assertEqual(recover.call_count,2);self.assertIn(A,c.failed);self.assertNotIn(B,c.failed)

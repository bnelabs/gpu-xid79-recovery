import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import gpu_recovery as g

A='0000:01:00.0'
B='0000:02:00.0'
MEMBERS={A:'GPU-11111111-2222-3333-4444-555555555555',
         B:'GPU-22222222-2222-3333-4444-555555555555'}


class IndependentHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        backend={'prefer_backend':True,'domain':{'members':MEMBERS,'health_command':['/fixture/health']}}
        self.cfg={'watch_gpus':[A,B],'backends':{A:backend,B:backend}}
        self.trust=patch.object(g,'trusted_file',side_effect=lambda p:pathlib.Path(p))
        self.trust.start();self.addCleanup(self.trust.stop)
        self.states={A:'healthy',B:'healthy'}
        self.change=lambda v:None
        self.command=None
        self.rc=0
        self.raw=None
        self.calls=[]
        self.runner=patch.object(g,'run',side_effect=self.helper)
        self.runner.start();self.addCleanup(self.runner.stop)
        self.nvml=patch.object(g,'nvml_identity',side_effect=AssertionError('NVML must not run'))
        self.nvml.start();self.addCleanup(self.nvml.stop)

    def controller(self):
        return g.Watcher(self.cfg,pathlib.Path(self.temp.name)/'budget.json')

    def helper(self,argv,timeout,env):
        self.calls.append((argv,timeout,env))
        self.assertEqual(timeout,10)
        self.assertEqual(env['GPU_RECOVERY_PHASE'],'health')
        self.assertEqual(env['GPU_RECOVERY_INTENT'],'observe')
        self.assertEqual(json.loads(env['GPU_DOMAIN_MEMBERS']),MEMBERS)
        value={'schema':1,'nonce':env['GPU_RECOVERY_NONCE'],'boot_id':env['GPU_RECOVERY_BOOT_ID'],
               'members':MEMBERS,'independent_health':True,'states':self.states}
        self.change(value)
        return self.rc,self.raw if self.raw is not None else json.dumps(value)

    def test_healthy_independent_observation_skips_nvml_and_reset(self):
        with patch.object(g,'recover_bdf') as recover:
            c=self.controller();c.check(A);recover.assert_not_called()
            self.assertEqual(c.identities,MEMBERS)
        self.assertEqual(len(self.calls),1)

    def test_peer_fault_routes_whole_domain_once_and_charges_both(self):
        self.states[B]='recoverable_fault'
        with patch.object(g,'Services') as services,patch.object(g,'recover_bdf',return_value=[A,B]) as recover:
            c=self.controller();c.check(A)
            recover.assert_called_once_with(A,c.cfg)
            self.assertEqual(services.return_value.stop.call_count,2)
            self.assertEqual([len(c.attempts[m]) for m in MEMBERS],[1,1])

    def test_hold_peer_blocks_otherwise_recoverable_fault(self):
        self.states={A:'recoverable_fault',B:'hold'}
        self.assert_no_reset()

    def assert_no_reset(self):
        with patch.object(g,'Services') as services,patch.object(g,'recover_bdf') as recover:
            c=self.controller();c.check(A);recover.assert_not_called()
            self.assertEqual(c.failed,set(MEMBERS))
            self.assertEqual(services.return_value.stop.call_count,2)
            self.assertFalse(any(c.attempts.values()))

    def test_receipt_identity_freshness_and_strict_types(self):
        for key,value in [('schema',True),('nonce','old'),('boot_id','other'),
                          ('members',{}),('independent_health',1),('states',{A:'healthy'}),
                          ('states',{A:'fault',B:'healthy'}),('states',[]),('states',{A:[],B:'healthy'})]:
            with self.subTest(key=key,value=value):
                self.change=lambda v,key=key,value=value:v.update({key:value})
                self.assert_no_reset()

    def test_failed_malformed_or_blocked_helper_never_falls_back(self):
        for raw in ('not-json','[]','null'):
            self.raw=raw;self.assert_no_reset()
        self.raw=None;self.rc=1;self.assert_no_reset()
        self.runner.stop()
        with patch.object(g,'run',side_effect=g.CommandTimeout('blocked')):
            self.assert_no_reset()

    def test_untrusted_command_never_executes(self):
        with patch.object(g,'trusted_file',side_effect=g.RecoveryError('untrusted')):
            with self.assertRaises(g.HealthUnavailable):
                # Construct under the ordinary test trust, then change trust.
                c=object.__new__(g.Watcher);c.cfg=self.cfg;c.health_identity(A)
        self.assertEqual(self.calls,[])

    def test_invalid_helper_configuration(self):
        for command in ([],['relative'],['/fixture/health',None],'string'):
            self.cfg['backends'][A]['domain']['health_command']=command
            self.assert_no_reset()

    def test_missing_independent_opt_in_keeps_existing_nvml_path(self):
        self.cfg['backends'][A]['domain'].pop('health_command')
        with patch.object(g,'nvml_identity',return_value=MEMBERS[A]) as nvml:
            self.assertEqual(self.controller().health_identity(A),MEMBERS[A]);nvml.assert_called_once_with(A)
        self.assertEqual(self.calls,[])

    def test_each_observation_has_a_fresh_nonce(self):
        c=self.controller();c.health_identity(A);c.health_identity(A)
        self.assertNotEqual(self.calls[0][2]['GPU_RECOVERY_NONCE'],self.calls[1][2]['GPU_RECOVERY_NONCE'])

    def test_boot_change_during_observation_is_held(self):
        c=self.controller()
        native=g.text
        calls=iter(['boot-before','boot-after'])
        with patch.object(g,'text',side_effect=lambda p,*a:next(calls) if str(p)=='/proc/sys/kernel/random/boot_id' else native(p,*a)):
            with self.assertRaises(g.HealthUnavailable):c.health_identity(A)

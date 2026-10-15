import http.server
import json
import pathlib
import threading
import tempfile
import unittest
from unittest.mock import patch

import gpu_recovery as g
from adapters.redfish_gpu import Adapter, Client, resource_path, discover_processors

BDF = '0000:01:00.0'
UUID = 'GPU-11111111-2222-3333-4444-555555555555'
RESOURCE = '/redfish/v1/Systems/Node/Processors/GPU0'
ACTION = RESOURCE + '/Actions/Processor.Reset'


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.events.append(('GET', self.path))
        self.answer()

    def do_POST(self):
        self.server.events.append(('POST', self.path))
        self.server.bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.answer()

    def answer(self):
        code, headers, value = self.server.reply(self.command, self.path)
        self.send_response(code)
        for key, item in headers.items():
            self.send_header(key, item)
        self.end_headers()
        if value is not None:
            self.wfile.write(json.dumps(value).encode())


class RedfishTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.events = []
        self.server.bodies = []
        self.server.reply = lambda method, path: (200, {}, {})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = {'schema': 1, 'lab': True, 'state_dir': self.temp.name,
            'endpoint': f'http://127.0.0.1:{self.server.server_port}', 'task_timeout': .05,
            'targets': {BDF: {'uuid': UUID, 'resource': RESOURCE,
                'serial': 'SERIAL-0', 'reset_type': 'ForceRestart'}}}
        self.client = Client(self.cfg)

    def adapter(self):
        native = g.text
        with patch.object(g, 'text', side_effect=lambda p, *a: 'gpu_recovery_lab=1' if str(p) == '/proc/cmdline' else native(p, *a)):
            return Adapter(self.cfg)

    def gpu(self):
        return {'@odata.id': RESOURCE, 'ProcessorType': 'GPU', 'SerialNumber': 'SERIAL-0',
                'Actions': {'#Processor.Reset': {'target': ACTION,
                    'ResetType@Redfish.AllowableValues': ['ForceRestart']}}}

    def test_http_cannot_target_non_loopback_or_use_lab_without_guest_marker(self):
        for endpoint in ('http://example.com', 'http://localhost', 'https://user:secret@example.com', 'https://example.com/redfish/v1'):
            with self.assertRaises(g.RecoveryError):
                Client(dict(self.cfg, endpoint=endpoint))
        with self.assertRaisesRegex(g.RecoveryError, 'guest kernel marker'):
            Adapter(self.cfg)

    def test_link_scope_and_encoded_paths_rejected(self):
        for path in ('https://evil.test/redfish/v1/Tasks/1', '//evil/redfish/v1/Tasks/1',
                     '/redfish/v1/../Systems', '/redfish/v1/Systems%2fNode', '/redfish/v1//Systems',
                     '/redfish/v1/Systems?next=remote'):
            with self.assertRaises(g.RecoveryError):
                resource_path(path)

    def test_redirect_never_followed_or_post_retried(self):
        self.server.reply = lambda method, path: (307, {'Location': self.cfg['endpoint']+'/redfish/v1/other'}, {})
        with self.assertRaisesRegex(g.RecoveryError, 'HTTP 307'):
            self.client.reset(ACTION, 'ForceRestart')
        self.assertEqual(self.server.events, [('POST', ACTION)])

    def test_synchronous_success_is_one_post(self):
        self.server.reply = lambda method, path: (204, {}, None)
        self.client.reset(ACTION, 'ForceRestart')
        self.assertEqual(self.server.events, [('POST', ACTION)])
        self.assertEqual(self.server.bodies, [{'ResetType': 'ForceRestart'}])

    def test_async_task_success(self):
        task = '/redfish/v1/TaskService/Tasks/1'
        self.server.reply = lambda method, path: (202, {'Location': task}, {}) if method == 'POST' else (200, {}, {'TaskState': 'Completed', 'TaskStatus': 'OK'})
        self.client.reset(ACTION, 'ForceRestart')
        self.assertEqual(self.server.events, [('POST', ACTION), ('GET', task)])

    def test_async_failure_warning_and_timeout_do_not_retry(self):
        task = '/redfish/v1/TaskService/Tasks/1'
        for value in ({'TaskState': 'Exception'}, {'TaskState': 'Completed', 'TaskStatus': 'Warning'},
                      {'TaskState': 'Running'}):
            self.server.events.clear()
            self.server.reply = lambda method, path: (202, {'Location': task}, {}) if method == 'POST' else (200, {}, value)
            with self.assertRaises(g.RecoveryError):
                self.client.reset(ACTION, 'ForceRestart')
            self.assertEqual(sum(method == 'POST' for method, _ in self.server.events), 1)

    def test_cross_origin_monitor_refused_without_get(self):
        self.server.reply = lambda method, path: (202, {'Location': 'https://other.test/redfish/v1/TaskService/Tasks/1'}, {})
        with self.assertRaises(g.RecoveryError):
            self.client.reset(ACTION, 'ForceRestart')
        self.assertEqual(self.server.events, [('POST', ACTION)])

    def test_processor_identity_capability_and_scope_are_checked(self):
        mutations = [lambda v: v.update(ProcessorType='CPU'),
            lambda v: v.update(SerialNumber='different'), lambda v: v.update({'@odata.id': RESOURCE+'1'}),
            lambda v: v['Actions']['#Processor.Reset'].update(target='/redfish/v1/Systems/Node/Actions/ComputerSystem.Reset'),
            lambda v: v['Actions']['#Processor.Reset'].update({'ResetType@Redfish.AllowableValues': []})]
        for mutate in mutations:
            value = self.gpu()
            mutate(value)
            self.server.reply = lambda method, path: (200, {}, value)
            with self.assertRaises(g.RecoveryError):
                self.adapter().inventory()
        self.assertFalse(any(method == 'POST' for method, _ in self.server.events))

    def test_actioninfo_must_agree_with_inline_capabilities(self):
        value = self.gpu()
        info = RESOURCE+'/ResetActionInfo'
        value['Actions']['#Processor.Reset']['@Redfish.ActionInfo'] = info
        self.server.reply = lambda method, path: (200, {}, value if path == RESOURCE else {'Parameters': [{'Name': 'ResetType', 'AllowableValues': ['GracefulRestart']}]})
        with self.assertRaisesRegex(g.RecoveryError, 'not advertised'):
            self.adapter().inventory()

    def test_duplicate_mapping_and_non_gpu_resource_refused(self):
        self.cfg['targets']['0000:02:00.0'] = dict(self.cfg['targets'][BDF])
        with self.assertRaises(g.RecoveryError):
            self.adapter()
        self.cfg['targets'].pop('0000:02:00.0')
        self.cfg['targets'][BDF]['resource'] = '/redfish/v1/Systems/Node'
        with self.assertRaises(g.RecoveryError):
            self.adapter()

    def test_read_only_discovery_follows_collections_and_skips_cpus(self):
        systems = '/redfish/v1/Systems'
        node = systems+'/Node'
        processors = node+'/Processors'
        values = {systems: {'Members': [{'@odata.id': node}]},
            node: {'Processors': {'@odata.id': processors}},
            processors: {'Members': [{'@odata.id': RESOURCE}, {'@odata.id': processors+'/CPU0'}]},
            RESOURCE: self.gpu(), processors+'/CPU0': {'@odata.id': processors+'/CPU0', 'ProcessorType': 'CPU'}}
        self.server.reply = lambda method, path: (200, {}, values[path])
        result = discover_processors(self.client)
        self.assertEqual([v['serial'] for v in result['gpus']], ['SERIAL-0'])
        self.assertFalse(result['gpus'][0]['reset_scope_verified'])
        self.assertTrue(all(method == 'GET' for method, _ in self.server.events))

    def test_cyclic_discovery_pagination_is_refused(self):
        self.server.reply = lambda method, path: (200, {}, {'Members': [], 'Members@odata.nextLink': path})
        with self.assertRaisesRegex(g.RecoveryError, 'Cyclic'):
            discover_processors(self.client)
        self.assertEqual(len(self.server.events), 1)

    def test_recover_requires_lifecycle_helper_before_any_post(self):
        self.server.reply = lambda method, path: (200, {}, self.gpu())
        env = {'GPU_DOMAIN_MEMBERS': json.dumps({BDF: UUID}), 'GPU_DOMAIN_SCOPE': json.dumps([BDF]),
            'GPU_RECOVERY_BOOT_ID': g.text('/proc/sys/kernel/random/boot_id'),
            'GPU_RECOVERY_NONCE': 'fresh', 'GPU_BDF': BDF, 'GPU_RECOVERY_INTENT': 'recover'}
        with patch.object(g, 'trusted_file', side_effect=lambda p: pathlib.Path(p)):
            with self.assertRaisesRegex(g.RecoveryError, 'lifecycle command'):
                self.adapter().execute('recover', env)
        self.assertFalse(any(method == 'POST' for method, _ in self.server.events))

    def test_pending_reset_survives_new_adapter_and_requires_explicit_reconciliation(self):
        self.cfg['lifecycle_command'] = ['/fixture/lifecycle']
        self.server.reply = lambda method, path: (500, {}, {}) if method == 'POST' else (200, {}, self.gpu())
        env = {'GPU_DOMAIN_MEMBERS': json.dumps({BDF: UUID}), 'GPU_DOMAIN_SCOPE': json.dumps([BDF]),
            'GPU_RECOVERY_BOOT_ID': g.text('/proc/sys/kernel/random/boot_id'),
            'GPU_RECOVERY_NONCE': 'first', 'GPU_BDF': BDF, 'GPU_RECOVERY_INTENT': 'recover'}
        resolved = False
        def helper(argv, timeout, child_env):
            return 0, json.dumps({'schema': 1, 'nonce': child_env['GPU_RECOVERY_NONCE'],
                'boot_id': child_env['GPU_RECOVERY_BOOT_ID'], 'members': {BDF: UUID}, 'scope_bdfs': [BDF],
                'exclusive_local_control': True, 'host_reboot_required': False, 'clients_quiesced': True,
                'ready': True, 'workloads_verified': True, 'fabric_verified': True,
                'management_reset_resolved': resolved})
        with patch.object(g, 'trusted_file', side_effect=lambda p: pathlib.Path(p)), patch.object(g, 'run', side_effect=helper):
            with self.assertRaisesRegex(g.RecoveryError, 'HTTP 500'):
                self.adapter().execute('recover', env)
            original_events = list(self.server.events)
            env['GPU_RECOVERY_NONCE'] = 'second'
            with self.assertRaisesRegex(g.RecoveryError, 'Unresolved management reset'):
                self.adapter().execute('preflight', env)
            self.assertEqual(original_events, self.server.events)
            env['GPU_RECOVERY_INTENT'] = 'resume'
            self.adapter().execute('preflight', env)
            with self.assertRaisesRegex(g.RecoveryError, 'not reconciled'):
                self.adapter().execute('validation', env)
            path = pathlib.Path(self.temp.name)/'operations.json'
            self.assertTrue(json.loads(path.read_text())['operations'])
            resolved = True
            self.adapter().execute('validation', env)
            self.assertFalse(json.loads(path.read_text())['operations'])
        self.assertEqual(sum(method == 'POST' for method, _ in self.server.events), 1)

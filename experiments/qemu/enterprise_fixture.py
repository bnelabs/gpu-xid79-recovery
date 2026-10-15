#!/usr/bin/env python3
"""Guest-only BMC, lifecycle and service models. Never a hardware backend."""
import http.server
import json
import os
import pathlib
import sys
import threading

sys.path.insert(0, '/lab')
import gpu_recovery as g

STATE = pathlib.Path('/run/enterprise-fixture.json')
EVENTS = pathlib.Path('/run/enterprise-events.jsonl')


def state():
    return json.loads(STATE.read_text())


def event(**value):
    with EVENTS.open('a') as stream:
        stream.write(json.dumps(value) + '\n')


class BMC(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value=None, location=None):
        self.send_response(status)
        if location:
            self.send_header('Location', location)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if value is not None:
            self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        cfg = state()
        if self.path.startswith('/redfish/v1/TaskService/Tasks/'):
            if cfg['fault'] == 'task-failed':
                return self.reply(200, {'TaskState': 'Exception', 'TaskStatus': 'Critical'})
            if cfg['fault'] == 'task-timeout':
                return self.reply(200, {'TaskState': 'Running', 'TaskStatus': 'OK'})
            return self.reply(200, {'TaskState': 'Completed', 'TaskStatus': 'OK'})
        for index, (bdf, uuid) in enumerate(sorted(cfg['members'].items())):
            resource = f'/redfish/v1/Systems/Lab/Processors/GPU{index}'
            if self.path != resource:
                continue
            cpu = cfg['fault'] == 'cpu-resource' and index == 0
            action = resource + '/Actions/Processor.Reset'
            if cfg['fault'] == 'node-action' and index == 0:
                action = '/redfish/v1/Systems/Lab/Actions/ComputerSystem.Reset'
            return self.reply(200, {'@odata.id': resource, 'ProcessorType': 'CPU' if cpu else 'GPU',
                'SerialNumber': 'wrong' if cfg['fault'] == 'wrong-serial' and index == 0 else f'LAB-GPU-{index}',
                'Status': {'State': 'Enabled', 'Health': 'OK'},
                'Actions': {'#Processor.Reset': {'target': action,
                    'ResetType@Redfish.AllowableValues': [] if cfg['fault'] == 'unsupported' else ['ForceRestart']}}})
        self.reply(404, {'error': 'unknown fixture resource'})

    def do_POST(self):
        cfg = state()
        for index, bdf in enumerate(sorted(cfg['members'])):
            if self.path != f'/redfish/v1/Systems/Lab/Processors/GPU{index}/Actions/Processor.Reset':
                continue
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if data != {'ResetType': 'ForceRestart'}:
                return self.reply(400)
            event(kind='reset', bdf=bdf)
            if cfg['fault'] == 'partial-reset' and index == 2:
                return self.reply(500)
            # Model the CPLD's sideband reset using a real guest-kernel bridge
            # reset. Sibling testdevs share reset just as the declared domain says.
            parent = (g.SYSFS / bdf).resolve().parent
            g.write_sysfs(parent / 'reset_subordinate', '1')
            if cfg['fault'] == 'dropped-response':
                self.connection.shutdown(2)
                self.connection.close()
                return
            self.reply(202, {'TaskState': 'Running'}, '/redfish/v1/TaskService/Tasks/' + str(index))
            return
        self.reply(404)


def serve():
    server = http.server.HTTPServer(('127.0.0.1', 0), BMC)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def lifecycle():
    cfg = state()
    members = json.loads(os.environ['GPU_DOMAIN_MEMBERS'])
    scope = json.loads(os.environ['GPU_DOMAIN_SCOPE'])
    # Independent fixture state is the modeled authority. Do not accept the
    # caller's member set just because it was passed through the environment.
    if members != cfg['members']:
        raise RuntimeError('Fixture membership differs')
    actual = set()
    for bdf in members:
        device = g.SYSFS / bdf
        if g.text(device / 'vendor') != '0x1b36' or g.text(device / 'device') != '0x0005':
            raise RuntimeError('Fixture device differs')
        actual.update(p.name for p in device.resolve().parent.rglob('*') if g.BDF.fullmatch(p.name))
    if set(scope) != actual:
        raise RuntimeError('Fixture reset scope differs')
    if any((pathlib.Path('/run/enterprise-services') / unit).exists() for unit in cfg['services']):
        raise RuntimeError('Synthetic workloads still running')
    phase = os.environ['GPU_RECOVERY_PHASE']
    event(kind='lifecycle', phase=phase)
    value = {'schema': 1, 'nonce': os.environ['GPU_RECOVERY_NONCE'],
        'boot_id': g.text('/proc/sys/kernel/random/boot_id'), 'members': cfg['members'],
        'scope_bdfs': sorted(actual), 'exclusive_local_control': True,
        'host_reboot_required': False, 'clients_quiesced': True, 'ready': True}
    if cfg['fault'] == 'ownership-denied':
        value['exclusive_local_control'] = False
    if phase == 'validation':
        value.update(workloads_verified=True, fabric_verified=cfg['fault'] != 'fabric-failed')
        if cfg['fault'] == 'stale-validation':
            value['nonce'] = 'previous-operation'
    print(json.dumps(value))


if __name__ == '__main__':
    if 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
        raise RuntimeError('Disposable guest kernel marker required')
    lifecycle()

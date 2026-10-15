#!/usr/bin/env python3
"""Optional Redfish GPU reset adapter; requires a platform lifecycle helper.

Only explicitly provisioned GPU Processor.Reset actions are supported. No node,
chassis, switch, firmware-update or guessed OEM action is ever requested.
"""
import argparse
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_recovery as g
import domain_adapter as domain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resource_path(value):
    # Restrict links before URL joining: encoded separators/dot segments and
    # arbitrary authorities must never escape the provisioned BMC.
    if not isinstance(value, str) or not re.fullmatch(r'/redfish/v1/[A-Za-z0-9_./-]+', value):
        raise g.RecoveryError('Expected a canonical local Redfish resource path')
    if any(part in ('', '.', '..') for part in value.split('/')[1:]):
        raise g.RecoveryError('Noncanonical Redfish resource path')
    return value


class Client:
    def __init__(self, cfg):
        endpoint = cfg.get('endpoint', '')
        url = urllib.parse.urlsplit(endpoint)
        lab = cfg.get('lab') is True
        if (url.scheme != 'https' and not (lab and url.scheme == 'http' and url.hostname == '127.0.0.1')):
            raise g.RecoveryError('HTTPS required; lab HTTP is limited to numeric loopback')
        if not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise g.RecoveryError('Endpoint must be a BMC origin without credentials or path')
        self.origin = endpoint.rstrip('/')
        self.timeout = cfg.get('request_timeout', 5)
        self.task_timeout = cfg.get('task_timeout', 30)
        if any(type(v) not in (int, float) or not 0 < v <= 45 for v in (self.timeout, self.task_timeout)):
            raise g.RecoveryError('Timeouts must be greater than zero and at most 45 seconds')
        ca_file = str(g.trusted_file(cfg['ca_file'])) if cfg.get('ca_file') else None
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPSHandler(context=context))
        self.token = None
        if cfg.get('token_file'):
            self.token = g.trusted_file(cfg['token_file']).read_text().strip()
            if not self.token or '\r' in self.token or '\n' in self.token:
                raise g.RecoveryError('Invalid Redfish token file')
        elif not lab:
            raise g.RecoveryError('Provision a root-managed Redfish session token file')
        self.deadline = time.monotonic() + 45

    def request(self, method, path, payload=None, deadline=None):
        path = resource_path(path)
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['X-Auth-Token'] = self.token
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        timeout = self.timeout
        operation_deadline = min(self.deadline or float('inf'), deadline or float('inf'))
        if operation_deadline != float('inf'):
            timeout = min(timeout, operation_deadline - time.monotonic())
            if timeout <= 0:
                raise g.CommandTimeout('Redfish operation deadline exceeded; outcome may be indeterminate')
        req = urllib.request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise g.RecoveryError('Redfish response exceeds limit')
                value = json.loads(body) if body else {}
                if not isinstance(value, dict):
                    raise g.RecoveryError('Redfish response must be an object')
                return response.status, dict(response.headers), value
        except urllib.error.HTTPError as error:
            # Never print remote bodies or headers, which can contain credentials.
            code = error.code
            error.close()
            raise g.RecoveryError(f'Redfish HTTP {code}; no action retried') from None
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise g.CommandTimeout('Redfish transport failed; action outcome may be indeterminate; no retry') from None
        except (ValueError, UnicodeError):
            raise g.RecoveryError('Malformed Redfish response; no action retried') from None

    def get(self, path):
        status, _, value = self.request('GET', path)
        if status != 200:
            raise g.RecoveryError('Redfish GET did not return HTTP 200')
        return value

    def reset(self, path, reset_type):
        status, headers, _ = self.request('POST', path, {'ResetType': reset_type})
        if status in (200, 204):
            return
        if status != 202:
            raise g.RecoveryError('Unexpected reset response; no action retried')
        # Poll the same-origin task monitor. Redirects and remote Location URLs
        # are refused; POST is never replayed after a lost response.
        location = next((v for k, v in headers.items() if k.lower() == 'location'), None)
        task = resource_path(location)
        if not task.startswith('/redfish/v1/TaskService/'):
            raise g.RecoveryError('Reset monitor is outside TaskService; outcome indeterminate')
        deadline = min(self.deadline or float('inf'), time.monotonic() + self.task_timeout)
        while time.monotonic() < deadline:
            status, _, value = self.request('GET', task, deadline=deadline)
            if status == 204:
                return
            if status not in (200, 202):
                raise g.RecoveryError('Unexpected task monitor status')
            state = value.get('TaskState')
            if state == 'Completed':
                if value.get('TaskStatus') not in ('OK',):
                    raise g.RecoveryError('Redfish reset task completed without OK status')
                return
            if state not in ('New', 'Starting', 'Running', 'Pending'):
                raise g.RecoveryError('Redfish reset task failed or returned unknown state')
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        raise g.CommandTimeout('Redfish reset task timed out; no reset retry or fallback')


def discover_processors(client):
    """Bounded read-only BMC enumeration; never creates trusted target mappings."""
    def collection(path):
        seen = set()
        result = []
        while path:
            path = resource_path(path)
            if path in seen or len(seen) >= 16:
                raise g.RecoveryError('Cyclic or excessive Redfish collection pagination')
            seen.add(path)
            value = client.get(path)
            for item in value.get('Members', []):
                result.append(resource_path(item.get('@odata.id')))
                if len(result) > 128:
                    raise g.RecoveryError('Redfish collection exceeds discovery limit')
            path = value.get('Members@odata.nextLink')
        if len(set(result)) != len(result):
            raise g.RecoveryError('Duplicate Redfish collection members')
        return result
    result = []
    for system_path in collection('/redfish/v1/Systems'):
        if not re.fullmatch(r'/redfish/v1/Systems/[^/]+', system_path):
            raise g.RecoveryError('Unexpected system collection member')
        system = client.get(system_path)
        processors = system.get('Processors', {}).get('@odata.id')
        if not processors:
            continue
        if processors != system_path + '/Processors':
            raise g.RecoveryError('Processor collection escapes the discovered system')
        for path in collection(processors):
            if not re.fullmatch(re.escape(processors) + r'/[^/]+', path):
                raise g.RecoveryError('Unexpected processor collection member')
            value = client.get(path)
            if value.get('@odata.id') != path:
                raise g.RecoveryError('Discovered processor identity mismatch')
            if value.get('ProcessorType') == 'GPU':
                action = value.get('Actions', {}).get('#Processor.Reset', {})
                result.append({'resource': path, 'serial': value.get('SerialNumber'),
                    'manufacturer': value.get('Manufacturer'), 'model': value.get('Model'),
                    'advertised_action': action, 'status': value.get('Status'),
                    'physical_gpu_uuid_mapping': 'requires independent provisioning',
                    'reset_scope_verified': False})
                if len(result) > 128:
                    raise g.RecoveryError('GPU discovery exceeds limit')
    return {'inventory_only': True, 'gpus': result, 'hardware_recovery_verified': False}


class Adapter:
    def __init__(self, cfg):
        self.cfg = cfg
        if type(cfg.get('schema')) is not int or cfg['schema'] != 1:
            raise g.RecoveryError('Adapter configuration requires schema 1')
        if cfg.get('lab') is True and 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
            raise g.RecoveryError('Lab mode requires a disposable guest kernel marker')
        self.client = Client(cfg)
        self.targets = cfg.get('targets')
        if not isinstance(self.targets, dict) or not self.targets:
            raise g.RecoveryError('Explicit PCI-to-Redfish target mapping required')
        resources = set()
        serials = set()
        for bdf, item in self.targets.items():
            if not isinstance(bdf, str) or not g.BDF.fullmatch(bdf) or not isinstance(item, dict):
                raise g.RecoveryError('Invalid target mapping')
            if not isinstance(item.get('uuid'), str) or not domain.UUID.fullmatch(item['uuid']):
                raise g.RecoveryError('Provision physical GPU UUID')
            path = resource_path(item.get('resource'))
            if not re.fullmatch(r'/redfish/v1/Systems/[^/]+/Processors/[^/]+', path):
                raise g.RecoveryError('Only a directly mapped Processor resource is supported')
            serial = item.get('serial')
            if not isinstance(serial, str) or not serial.strip() or serial in serials or path in resources:
                raise g.RecoveryError('Provision unique nonempty GPU serials and resource paths')
            if item.get('reset_type') not in ('ForceRestart', 'GracefulRestart'):
                raise g.RecoveryError('Only restart actions are supported')
            resources.add(path)
            serials.add(serial)

    def inventory(self):
        result = {}
        for bdf, item in sorted(self.targets.items()):
            value = self.client.get(item['resource'])
            if value.get('@odata.id') != item['resource'] or value.get('ProcessorType') != 'GPU':
                raise g.RecoveryError('Mapped resource is not the expected GPU Processor')
            if value.get('SerialNumber') != item['serial']:
                raise g.RecoveryError('GPU serial differs from provisioned identity')
            if value.get('Status', {}).get('State') in ('Absent', 'UnavailableOffline', 'Disabled'):
                raise g.RecoveryError('Mapped GPU is not available to the management controller')
            action = value.get('Actions', {}).get('#Processor.Reset', {})
            target = resource_path(action.get('target'))
            if target != item['resource'] + '/Actions/Processor.Reset':
                raise g.RecoveryError('Reset action escapes the mapped GPU resource')
            allowed = action.get('ResetType@Redfish.AllowableValues')
            if '@Redfish.ActionInfo' in action:
                info = self.client.get(resource_path(action['@Redfish.ActionInfo']))
                parameters = [p for p in info.get('Parameters', []) if p.get('Name') == 'ResetType']
                if len(parameters) != 1:
                    raise g.RecoveryError('ResetType ActionInfo is missing or ambiguous')
                info_allowed = parameters[0].get('AllowableValues', [])
                allowed = info_allowed if allowed is None else [v for v in allowed if v in info_allowed]
            if not isinstance(allowed, list) or item['reset_type'] not in allowed:
                raise g.RecoveryError('Configured GPU restart is not advertised by this BMC')
            result[bdf] = {'uuid': item['uuid'], 'serial': item['serial'], 'resource': item['resource'],
                           'action': target, 'reset_type': item['reset_type']}
        return result

    def execute(self, phase, env):
        if phase not in ('preflight', 'recover', 'validation'):
            raise g.RecoveryError('Unknown adapter phase')
        approved = {bdf: item['uuid'] for bdf, item in self.targets.items()}
        try:
            members = json.loads(env['GPU_DOMAIN_MEMBERS'])
            scope = json.loads(env['GPU_DOMAIN_SCOPE'])
            boot, nonce = env['GPU_RECOVERY_BOOT_ID'], env['GPU_RECOVERY_NONCE']
        except (KeyError, ValueError):
            raise g.RecoveryError('Controller domain environment required') from None
        if members != approved or not isinstance(scope, list) or not set(approved) <= set(scope):
            raise g.RecoveryError('Controller domain differs from provisioned GPU mapping')
        if boot != g.text('/proc/sys/kernel/random/boot_id') or not nonce or env.get('GPU_BDF') not in approved:
            raise g.RecoveryError('Invalid operation identity')
        # Keep the complete adapter invocation below the controller's 60-second
        # preflight/validation bounds, including all member queries and tasks.
        self.client.deadline = time.monotonic() + 45
        inventory = self.inventory()
        command = self.cfg.get('lifecycle_command')
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            raise g.RecoveryError('A platform lifecycle command is required')
        g.trusted_file(command[0])
        # The helper must independently establish ownership, affected scope,
        # client drain and, during validation, fabric/partition readiness.
        # Recheck preflight even in recover, using this fresh domain nonce.
        check_phase = 'validation' if phase == 'validation' else 'preflight'
        helper_env = dict(env, GPU_RECOVERY_PHASE=check_phase,
                          GPU_REDFISH_TARGETS=json.dumps(inventory, sort_keys=True))
        remaining = self.client.deadline - time.monotonic()
        if remaining <= 0:
            raise g.CommandTimeout('Adapter deadline expired before lifecycle check')
        # receipt uses a 60s command bound; use a narrower bound here so the
        # outer controller cannot time out while an inner helper keeps running.
        def bounded_run(argv, timeout, child_env):
            return g.run(argv, min(timeout, max(.01, remaining)), child_env)
        class Runner:
            RecoveryError = g.RecoveryError
            run = staticmethod(bounded_run)
        value = domain.receipt(Runner, command, helper_env, approved, scope, boot, nonce, check_phase)
        if phase == 'recover':
            if self.inventory() != inventory:
                raise g.RecoveryError('BMC identity or action changed after lifecycle check')
            for item in inventory.values():
                self.client.reset(item['action'], item['reset_type'])
            # A successful BMC action is not CUDA/fabric validation. The outer
            # controller performs both before releasing any workload hold.
            return {'reset_requests_completed': len(inventory), 'workloads_verified': False}
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--inventory', action='store_true')
    modes.add_argument('--discover', action='store_true', help='Enumerate BMC GPU processors without creating mappings or resetting')
    args = parser.parse_args()
    cfg = g.config(args.config, privileged=True)
    if cfg.get('lab') is True and 'gpu_recovery_lab=1' not in g.text('/proc/cmdline').split():
        raise g.RecoveryError('Lab mode requires a disposable guest kernel marker')
    if args.discover:
        print(json.dumps(discover_processors(Client(cfg)), sort_keys=True));return
    adapter = Adapter(cfg)
    result = adapter.inventory() if args.inventory else adapter.execute(os.environ.get('GPU_RECOVERY_PHASE'), os.environ)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

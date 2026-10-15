"""Durable intent and ambiguity tracking for management-controller resets."""
import fcntl
import json
import os
import pathlib
import tempfile


class Journal:
    def __init__(self, g, directory, origin, targets, scope):
        self.g = g
        self.directory = pathlib.Path(directory)
        if not self.directory.is_absolute():
            raise g.RecoveryError('Reset journal directory must be absolute')
        self.origin, self.targets, self.scope = origin, targets, scope
        self.path = self.directory/'operations.json'
        self.lock = None

    def __enter__(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.g.trusted_file(self.directory)
        self.lock = (self.directory/'operations.lock').open('a')
        try:
            self.g.trusted_file(self.directory/'operations.lock')
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.value = {'schema': 1, 'operations': {}}
            if self.path.exists():
                self.g.trusted_file(self.path)
                self.value = json.loads(self.path.read_text())
            if (not isinstance(self.value, dict) or type(self.value.get('schema')) is not int
                    or self.value['schema'] != 1 or not isinstance(self.value.get('operations'), dict)):
                raise self.g.RecoveryError('Invalid reset operation journal')
            self._validate_records()
            return self
        except Exception:
            self.lock.close()
            self.lock = None
            raise

    def __exit__(self, *args):
        self.lock.close()

    def _validate_records(self):
        for nonce, record in self.value['operations'].items():
            if (not isinstance(nonce, str) or not nonce or not isinstance(record, dict)
                    or not isinstance(record.get('origin'), str)
                    or not isinstance(record.get('targets'), dict) or not record['targets']
                    or not isinstance(record.get('scope'), list)
                    or not isinstance(record.get('completed'), list)
                    or record.get('stage') not in ('armed', 'request_pending', 'awaiting_validation')):
                raise self.g.RecoveryError('Invalid reset operation record')
            if any(not isinstance(item, dict) or not isinstance(item.get('resource'), str)
                   for item in record['targets'].values()):
                raise self.g.RecoveryError('Invalid journal target identity')

    def _save(self):
        descriptor, name = tempfile.mkstemp(prefix='.reset-', dir=self.directory)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(self.value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def overlaps(self):
        resources = {item['resource'] for item in self.targets.values()}
        return {nonce: record for nonce, record in self.value['operations'].items()
                if record['origin'] == self.origin and resources.intersection(
                    item['resource'] for item in record['targets'].values())}

    def check(self, phase, intent, nonce):
        pending = self.overlaps()
        if not pending:
            return
        if any(record['targets'] != self.targets or record['scope'] != self.scope
               for record in pending.values()):
            raise self.g.RecoveryError('Unresolved reset domain differs; restore its full original mapping and scope')
        # A fresh resume may inspect/reconcile the complete domain, but must not
        # reset it. Normal validation is admitted only for this operation's nonce.
        own_validation = (phase == 'validation' and set(pending) == {nonce}
                          and pending[nonce]['stage'] == 'awaiting_validation')
        if intent != 'resume' and not own_validation:
            raise self.g.RecoveryError('Unresolved management reset; verify and resume the complete domain before another reset')

    def arm(self, nonce, boot):
        if self.overlaps() or nonce in self.value['operations']:
            raise self.g.RecoveryError('Cannot arm an unresolved or reused reset operation')
        self.value['operations'][nonce] = dict(origin=self.origin, targets=self.targets, scope=self.scope,
            boot_id=boot, stage='armed', completed=[], active_resource=None)
        self._save()  # Persist the full domain before sending the first request.

    def pending(self, nonce, resource):
        record = self.value['operations'][nonce]
        record.update(stage='request_pending', active_resource=resource)
        self._save()

    def completed(self, nonce, resource):
        record = self.value['operations'][nonce]
        record['completed'].append(resource)
        record.update(active_resource=None)
        self._save()

    def awaiting_validation(self, nonce):
        self.value['operations'][nonce]['stage'] = 'awaiting_validation'
        self._save()

    def clear_verified(self):
        # Only called after the adapter's final lifecycle receipt succeeds. The
        # controller has already verified every member's identity and workload.
        for nonce in self.overlaps():
            del self.value['operations'][nonce]
        self._save()

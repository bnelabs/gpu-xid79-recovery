#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$EUID" != 0 ]]; then echo 'Run sudo ./install.sh' >&2; exit 1; fi
install -d -o root -g root -m 0755 /usr/local/lib/gpu-recovery-generic /etc/gpu-recovery
install -o root -g root -m 0755 "$task_root/gpu_recovery.py" /usr/local/lib/gpu-recovery-generic/gpu_recovery.py
ln -sfn /usr/local/lib/gpu-recovery-generic/gpu_recovery.py /usr/local/sbin/gpu-recovery
if [[ ! -e /etc/gpu-recovery/config.json ]]; then
    install -o root -g root -m 0644 "$task_root/examples/config.json" /etc/gpu-recovery/config.json
fi
install -o root -g root -m 0644 "$task_root/gpu-recovery-generic.service" /etc/systemd/system/gpu-recovery-generic.service
systemctl daemon-reload
echo 'Installed; no watcher enabled and no GPU reset performed.'

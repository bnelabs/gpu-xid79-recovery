#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_profile=''
if [[ "$#" != 0 ]]; then
    if [[ "$#" == 2 && "$1" == '--profile' && "$2" == 'ms7d25' ]]; then
        task_profile='ms7d25'
    else
        echo 'Usage: sudo ./install.sh [--profile ms7d25]' >&2; exit 1
    fi
fi
if [[ "$EUID" != 0 ]]; then echo 'Run sudo ./install.sh' >&2; exit 1; fi
install -d -o root -g root -m 0755 /usr/local/lib/gpu-recovery-generic /etc/gpu-recovery /etc/gpu-recovery/profiles.d
install -o root -g root -m 0755 "$task_root/gpu_recovery.py" /usr/local/lib/gpu-recovery-generic/gpu_recovery.py
ln -sfn /usr/local/lib/gpu-recovery-generic/gpu_recovery.py /usr/local/sbin/gpu-recovery
if [[ ! -e /etc/gpu-recovery/config.json ]]; then
    install -o root -g root -m 0644 "$task_root/examples/config.json" /etc/gpu-recovery/config.json
fi
install -o root -g root -m 0644 "$task_root/gpu-recovery-generic.service" /etc/systemd/system/gpu-recovery-generic.service
if [[ "$task_profile" == 'ms7d25' && ! -e /etc/gpu-recovery/profiles.d/ms7d25.json ]]; then
    install -o root -g root -m 0644 "$task_root/profiles/ms7d25.json" /etc/gpu-recovery/profiles.d/ms7d25.json
fi
systemctl daemon-reload
echo 'Installed; no watcher enabled and no GPU reset performed.'

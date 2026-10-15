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
install -d -o root -g root -m 0755 /usr/local/lib/gpu-recovery-generic /usr/local/lib/gpu-recovery-generic/references /etc/gpu-recovery /etc/gpu-recovery/profiles.d
install -o root -g root -m 0755 "$task_root/gpu_recovery.py" /usr/local/lib/gpu-recovery-generic/gpu_recovery.py
install -o root -g root -m 0644 "$task_root/domain_adapter.py" /usr/local/lib/gpu-recovery-generic/domain_adapter.py
install -o root -g root -m 0644 "$task_root/recovery_capsule.py" /usr/local/lib/gpu-recovery-generic/recovery_capsule.py
install -o root -g root -m 0644 "$task_root/service_assessment.py" /usr/local/lib/gpu-recovery-generic/service_assessment.py
install -d -o root -g root -m 0755 /usr/local/lib/gpu-recovery-generic/adapters
install -o root -g root -m 0755 "$task_root/adapters/redfish_gpu.py" /usr/local/lib/gpu-recovery-generic/adapters/redfish_gpu.py
install -o root -g root -m 0755 "$task_root/adapters/switchtec_gpu.py" /usr/local/lib/gpu-recovery-generic/adapters/switchtec_gpu.py
install -o root -g root -m 0644 "$task_root/adapters/reset_journal.py" /usr/local/lib/gpu-recovery-generic/adapters/reset_journal.py
install -o root -g root -m 0755 "$task_root/cuda_probe.py" /usr/local/lib/gpu-recovery-generic/cuda_probe.py
install -o root -g root -m 0755 "$task_root/firmware_policy.py" /usr/local/lib/gpu-recovery-generic/firmware_policy.py
install -o root -g root -m 0755 "$task_root/firmware_image.py" /usr/local/lib/gpu-recovery-generic/firmware_image.py
install -o root -g root -m 0644 "$task_root/firmware_runtime.py" /usr/local/lib/gpu-recovery-generic/firmware_runtime.py
install -o root -g root -m 0644 "$task_root/references/intel-adls-gpio.json" /usr/local/lib/gpu-recovery-generic/references/intel-adls-gpio.json
ln -sfn /usr/local/lib/gpu-recovery-generic/gpu_recovery.py /usr/local/sbin/gpu-recovery
ln -sfn /usr/local/lib/gpu-recovery-generic/firmware_policy.py /usr/local/sbin/gpu-firmware-policy
ln -sfn /usr/local/lib/gpu-recovery-generic/firmware_image.py /usr/local/sbin/gpu-firmware-audit
if [[ ! -e /etc/gpu-recovery/config.json ]]; then
    install -o root -g root -m 0644 "$task_root/examples/config.json" /etc/gpu-recovery/config.json
fi
install -o root -g root -m 0644 "$task_root/gpu-recovery-generic.service" /etc/systemd/system/gpu-recovery-generic.service
if [[ "$task_profile" == 'ms7d25' && ! -e /etc/gpu-recovery/profiles.d/ms7d25.json ]]; then
    install -o root -g root -m 0644 "$task_root/profiles/ms7d25.json" /etc/gpu-recovery/profiles.d/ms7d25.json
fi
systemctl daemon-reload
echo 'Installed; no watcher enabled and no GPU reset performed.'

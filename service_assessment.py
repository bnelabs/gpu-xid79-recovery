"""Read-only deployment gaps; no reset authorization or hardware support claims."""
import re


def assess(report, cfg):
    bdf = report['target']['bdf']
    nvidia = report['nvidia']
    expected = cfg.get('expected_gpu_uuids', {}).get(bdf)
    issues = []
    if bdf not in cfg.get('watch_gpus', []):
        issues.append('GPU is not in watch_gpus')
    if not isinstance(expected, str) or not re.fullmatch(r'GPU-[0-9a-fA-F-]{36}', expected):
        issues.append('Provision a physical GPU UUID in expected_gpu_uuids')
    elif nvidia.get('uuid') and expected != nvidia['uuid']:
        issues.append('Provisioned UUID differs from observed GPU UUID')
    if not cfg.get('services') and not cfg.get('gpu_services', {}).get(bdf):
        issues.append('No workload services configured; client drain is not established')
    backend = report.get('selected_backend')
    validator = cfg.get('validation_command') or report.get('validation_command')
    if not validator and not cfg.get('cuda_validation', True):
        issues.append('CUDA workload verification is disabled')
    fallback = report['kernel_fallback_assessment']
    if fallback['blocked_by_existing_guards'] and not backend:
        issues.append('Kernel fallback is blocked; a lifecycle adapter is required if NVIDIA reset fails')
    unknown = list(fallback['unknown_nvidia_fields'])
    if unknown:
        issues.append('Establish unknown lifecycle state: ' + ', '.join(unknown))
    domain = report.get('lifecycle_domain')
    if domain and domain.get('configuration_error'):
        issues.append('Lifecycle domain configuration error: ' + domain['configuration_error'])
    if backend:
        issues.append('Selected adapter still requires runtime identity, scope and compatibility checks')
    return {
        'bdf': bdf,
        'observed_uuid': nvidia.get('uuid'),
        'config_space_reachable': report['config_space_reachable'],
        'configuration_gaps': issues,
        'routes': {
            'nvidia_reset': 'runtime attempt only; model/query success does not prove reset support',
            'kernel_fallback': fallback,
            'lifecycle_adapter_configured': bool(backend),
            'independent_reset_or_power_control': 'not established by discovery',
        },
        'firmware': {
            'driver_version': report.get('nvidia_version'),
            'gsp_version': nvidia.get('gsp_firmware'),
            'vbios_version': nvidia.get('vbios'),
            'rm_probe_role': 'diagnostics while driver transport works; not an independent lost-link reset',
        },
        'hardware_recovery_verified': False,
        'note': 'Assessment is inventory only. An empty gap list is not deployment approval or recovery certification.',
    }


def fleet(controller, cfg, gpu=None):
    """Assess watched and discovered devices, including missing provisioned GPUs.

    A timed-out driver command aborts the assessment: never stack further probes
    behind a potentially blocked operation. Ordinary per-device errors are retained.
    """
    found = controller.discover()
    targets = [gpu] if gpu else sorted(set(found) | set(cfg.get('watch_gpus', [])))
    results = []
    for bdf in targets:
        if not isinstance(bdf, str) or not controller.BDF.fullmatch(bdf):
            raise controller.RecoveryError('Assessment requires valid PCI addresses')
        if bdf not in found:
            results.append({'bdf': bdf, 'present': False,
                            'configuration_gaps': ['GPU missing from PCI discovery; automatic rediscovery is unsupported'],
                            'hardware_recovery_verified': False})
            continue
        try:
            report = controller.explore(controller.inspect(bdf), cfg)
            results.append(dict(assess(report, cfg), present=True))
        except controller.CommandTimeout:
            raise
        except (controller.RecoveryError, OSError, ValueError, TypeError) as error:
            results.append({'bdf': bdf, 'present': True, 'assessment_error': str(error),
                            'hardware_recovery_verified': False})
    return {'schema': 1, 'inventory_only': True, 'gpus': results,
            'limitations': ['No hardware reset, workload stop or CUDA test performed',
                            'Does not validate adapter implementation, tenant ownership or fabric health',
                            'Profile compatibility checks may run, as in explore; configuration and executables must be trusted']}

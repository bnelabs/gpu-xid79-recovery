#!/usr/bin/env python3
"""Inventory local management transports without sending device commands."""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_recovery as recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu', help='PCI address; defaults to the only discovered GPU')
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    devices = recovery.discover()
    selected = args.gpu or (devices[0] if len(devices) == 1 else None)
    report = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'kind': 'Local inventory only; no management commands or reset',
              'discovered_gpus': devices, 'selected_gpu': selected, 'errors': []}
    if selected:
        try:
            report['management'] = recovery.management_inventory(recovery.inspect(selected))
        except (OSError, recovery.RecoveryError) as exc:
            report['errors'].append(str(exc))
    else:
        report['errors'].append('No unique GPU selected; target-relative inventory skipped')
    try:
        entries = list(pathlib.Path('/sys/firmware/dmi/entries').iterdir())
        report['smbios_management_host_interface_records'] = sorted(p.name for p in entries if p.name.startswith('42-'))
    except OSError as exc:
        report['smbios_management_host_interface_records'] = None
        report['errors'].append(str(exc))
    report['ipmi_devices'] = sorted(p.name for p in pathlib.Path('/dev').glob('ipmi*'))
    try:
        result = subprocess.run(['busctl', '--system', '--no-pager', '--no-legend', 'list'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        report['management_dbus_services'] = sorted({line.split()[0] for line in result.stdout.splitlines()
                                                    if line.split() and any(term in line.split()[0].lower()
                                                                           for term in ('mctp', 'nsmd', 'pldm'))})
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report['management_dbus_services'] = None
        report['errors'].append(str(exc))
    report['interpretation'] = 'Inventory does not establish endpoint identity, supported reset commands, reset scope, or reachability after PCIe failure. Empty results do not exclude external BMC connectivity.'
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    main()

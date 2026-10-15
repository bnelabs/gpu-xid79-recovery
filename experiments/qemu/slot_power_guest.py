#!/usr/bin/env python3
"""Guest-only slot-power lifecycle experiment; does not model a physical GPU."""
import json
import pathlib
import sys
import time
import traceback


def main():
    if 'gpu_recovery_lab=1' not in pathlib.Path('/proc/cmdline').read_text().split():
        raise RuntimeError('Guest lab marker required')
    sys.path.insert(0, '/lab')
    import gpu_recovery as g
    root = pathlib.Path('/sys/bus/pci/devices')
    devices = sorted(p for p in root.iterdir() if g.text(p/'vendor') == '0x1b36' and g.text(p/'device') == '0x0005')
    if len(devices) != 2:
        raise RuntimeError('Expected two synthetic PCI devices')
    target, peer = devices
    g.discover = lambda root=root: [target.name]
    selected = g.inspect(target.name)
    inventory = g.hotplug_inventory(selected)
    slots = [slot for slot in inventory if slot['power_path'] and slot['target_functions_only']]
    if len(slots) != 1:
        raise RuntimeError('Expected one isolated target slot power control: ' + repr(inventory))
    slot = slots[0]
    boot = g.text('/proc/sys/kernel/random/boot_id')
    peer_identity = g.text(peer/'vendor'), g.text(peer/'device')
    g.write_sysfs(pathlib.Path(slot['power_path']), '0')
    time.sleep(1)
    removed = not target.exists()
    on_error = None
    try:
        g.write_sysfs(pathlib.Path(slot['power_path']), '1')
    except g.RecoveryError as exc:
        on_error = str(exc)
    time.sleep(1)
    returned = target.exists()
    peer_unchanged = peer.exists() and peer_identity == (g.text(peer/'vendor'), g.text(peer/'device'))
    same_boot = g.text('/proc/sys/kernel/random/boot_id') == boot
    print('GPU_QEMU_RESULT=' + json.dumps({
        'kind': 'Guest Linux slot-power experiment, synthetic endpoints; not NVIDIA recovery',
        'architecture': __import__('os').uname().machine,
        'kernel': __import__('os').uname().release,
        'slot_inventory': inventory, 'target_removed_after_off': removed,
        'target_returned_after_on': returned, 'power_on_error': on_error,
        'peer_identity_unchanged': peer_unchanged, 'boot_unchanged': same_boot,
        'passed': removed and peer_unchanged and same_boot,
        'recovery_verified': False,
        'interpretation': 'Passing means slot shutdown removed the target without removing the peer; it does not mean power-on restored it or a physical GPU recovered.'
    }), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('GPU_QEMU_RESULT=' + json.dumps({'passed': False, 'error': traceback.format_exc()}), flush=True)

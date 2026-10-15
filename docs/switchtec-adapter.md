# Optional Switchtec domain adapter

`adapters/switchtec_gpu.py` implements a provisioned per-physical-port hot reset
using the Switchtec vendor CLI. It can avoid motherboard GPIO decoding on a
qualified managed-switch deployment. It does not add a management interface to
an ordinary motherboard or claim that hot reset fixes every inaccessible GPU.

The reviewed vendor revision is `99da3bc799b84ff53abc726c2fdea01379b3c687`.
Its `fabric port_control` command is documented in source as PAX-only; this is
not a backend for every Switchtec product. The exact firmware command and chosen
transport need independent qualification. The production adapter is installed
but neither selected nor enabled automatically. No switch hardware was accessed.

## Provisioning

```json
{
  "schema": 1,
  "controller_id": "PHYSICALLY-VERIFIED-SWITCH-SERIAL",
  "device": "/dev/switchtec0",
  "switchtec_command": ["/usr/local/bin/switchtec"],
  "lifecycle_command": ["/usr/local/libexec/site-switchtec-lifecycle"],
  "reset_hold_seconds": 0.1,
  "state_dir": "/var/lib/gpu-recovery-generic/switchtec",
  "targets": {
    "0000:41:00.0": {
      "uuid": "GPU-11111111-2222-3333-4444-555555555555",
      "physical_port": 8
    }
  }
}
```

This is an illustrative mapping, not an approved device, UUID or reset duration.
Use a root-managed configuration and trusted executable paths. The helper must
independently verify the transport resolves to that physical controller, command
support, timing, all downstream functions and all affected switch partitions and
hosts. `/dev/switchtec0` numbering alone is not identity or transport independence.
Use the same stable controller ID and journal directory for every invocation;
configuration aliases do not establish different physical controllers.

Configure the executable as the `command`, `domain.preflight_command` and
`domain.validation_command` in the [domain protocol](domain-adapters.md):

```json
["/usr/bin/python3", "/usr/local/lib/gpu-recovery-generic/adapters/switchtec_gpu.py",
 "--config", "/etc/gpu-recovery/switchtec.json"]
```

The backend still needs explicit platform match constraints, approved UUIDs and
`prefer_backend: true`. A prepared capsule is needed to recover devices already
missing from sysfs. Do not deploy this backend until its site lifecycle helper
and actual reset path have passed a dedicated hardware qualification campaign.

## Lifecycle receipt

In addition to normal domain environment variables, the helper receives
`GPU_SWITCHTEC_CONTROLLER_ID`, `GPU_SWITCHTEC_DEVICE`, `GPU_SWITCHTEC_TARGETS`
and `GPU_PENDING_RESETS`. These are requested mappings, not observations.
`GPU_SWITCHTEC_TARGETS` contains each BDF's UUID, physical port and stable resource
string, e.g. `switchtec://PHYSICALLY-VERIFIED-SWITCH-SERIAL/ports/8`.

Every helper receipt must satisfy the ordinary domain contract and contain:

- `switchtec_controller_id`: independently confirmed provisioned identity.
- `switchtec_targets`: the exact independently confirmed expanded mapping.
- `independent_reset_control: true` and `transport_survives_gpu_loss: true`.
- `switchtec_command_supported: true` and `all_partitions_exclusive: true`.
- `reset_released: true`: no reset remains asserted or asynchronously pending.

Missing-device receipts also require the exact `missing_functions` list.
Validation requires `workloads_verified` and `fabric_verified`. A resume that
clears a pending operation also requires `management_reset_resolved: true`.
Echoing environment variables without independent checks is not a valid helper.
No production site helper is supplied: scheduler/driver/fabric/partition handling
is specific to the actual deployment, including CPU–GPU coherent systems.

## Reset and ambiguity handling

The adapter checks preflight again in recover, then fsyncs the complete reset
domain before any CLI request. It pulses each unique physical port once, even
when several approved GPUs share it. The command is:

```text
switchtec fabric port_control DEVICE --phys_port_id PORT --control_type HOT_RESET --hot_reset_flag SET
switchtec fabric port_control DEVICE --phys_port_id PORT --control_type HOT_RESET --hot_reset_flag CLEAR
```

SET and CLEAR each have a 15-second command bound, within a 45-second invocation
budget. The qualified hold interval is configurable from .01 to 1 second; this
range is a software bound, not a vendor timing recommendation. No DISABLE,
whole-switch hard reset, guessed port, raw register write or power toggle is used.

A failure or timeout leaves the durable record and stops further commands. This
includes not sending CLEAR after an ambiguous SET: a qualified operator/helper
must reconcile actual controller state, pending commands and any asserted reset
before resuming. The adapter never assumes process termination cancelled a
hardware command. Reset release uncertainty may leave a port unusable until that
reconciliation, but does not release workloads or start another pulse. The same
controller/resource overlap blocks another invocation across process/host restart.
Cross-host mutual exclusion and cross-backend ownership remain helper obligations.

After all pulses, the domain controller performs scoped rediscovery if needed,
checks every member with a fresh workload, then invokes lifecycle validation.
The journal clears only on successful validation. Hot reset is not fundamental
reset or proof of complete power removal.

## Evidence

- Unit tests cover both pulse edges, timeout/failure persistence, explicit resume,
  same-port deduplication, mapping changes, receipt requirements and invalid input.
- `experiments/replay_switchtec_cli.py` builds the pinned vendor CLI and replaces
  device-open and MRPC access at link time. All 256 physical-port byte values and
  both SET/CLEAR flags reach the actual vendor parser and serializer: 512 cases.
  An injected library failure propagates to the CLI. No management device opens.
  The fourth reserved request byte is uninitialized in the reviewed vendor source
  and is deliberately excluded from assertions; this is not wire certification.
- `experiments/qemu/run_switchtec.py` runs ten fault cases each on x86-64 and ARM64
  with eight synthetic endpoints in four physical-port groups and unrelated NVMe.
  The production controller, adapter, journal and capsule code execute unchanged.
  A modeled CLEAR performs a real guest-kernel bridge reset. SET assertion,
  management identity, partition/fabric receipts and workloads are synthetic.
  Cases include all eight logically absent endpoints, lost SET response, failed
  CLEAR, process retry refusal, explicit reconciliation and validation failures.

These tests establish software orchestration only. They do not emulate switch
firmware, NVIDIA GPU firmware, electrical reset or physical Xid79 recovery.

```bash
python3 experiments/replay_switchtec_cli.py --vendor-repo /path/to/switchtec-user \
  --output /tmp/switchtec-cli.json
python3 experiments/qemu/run_switchtec.py --kernel /path/to/x86-kernel \
  --output /tmp/switchtec-x86.json
# ARM64 additionally needs --arch aarch64 and --userspace /path/to/arm64-root
```

Sources: [vendor CLI](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/cli/fabric.c),
[vendor serializer](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/lib/fabric.c),
[transport implementation](https://github.com/Microsemi/switchtec-user/blob/99da3bc799b84ff53abc726c2fdea01379b3c687/lib/switchtec.c).

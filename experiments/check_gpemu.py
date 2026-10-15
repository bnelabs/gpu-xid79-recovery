#!/usr/bin/env python3
"""Exercise pinned GPEmu timing classes on CPU; no GPU/driver recovery claim.

Load the reviewed upstream emulators.py directly, avoiding package-wide broker
imports. Uses CSV rows only, not regression pickle loading or custom PyTorch.
Run in a separate environment with pandas installed.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import time

REVISION = '27e9534ee0c3d594030e2519d4a418b3f0a7860f'
MODULE = 'gpemu/gpemu/gpemu/emulators.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    args = parser.parse_args()
    revision = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != REVISION:
        raise RuntimeError('Review the new GPEmu revision before execution')
    source = args.repo/MODULE
    pinned = subprocess.check_output(['git', '-C', str(args.repo), 'show', REVISION+':'+MODULE])
    if source.read_bytes() != pinned:
        raise RuntimeError('GPEmu module differs from pinned source')
    spec = importlib.util.spec_from_file_location('reviewed_gpemu_timing', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import pandas
    database = args.repo/'profiled_data'
    profiles = sorted(p.name for p in (database/'time/compute/forward').iterdir() if p.is_dir())
    hashes = {MODULE: hashlib.sha256(pinned).hexdigest()}
    cases = []
    for gpu in profiles:
        for mode in ('sync', 'async', 'cancel-and-restart'):
            config = dict(gpu=gpu, model='resnet18', batch_size=1, num_gpus=1,
                          from_databse=True, database_path=str(database))
            compute = module.ComputeTimeEmulator(**config, mode='async' if mode != 'sync' else 'sync')
            transfer = module.TransferTimeEmulator(**config, mode='async' if mode != 'sync' else 'sync')
            memory = module.GPUMemoryEmulator(gpu_cap=1000000, gpu=gpu, model='resnet18',
                        batch_size=1, database_path=str(database), num_gpus=1)
            expected = float(compute.get_forward_time()+compute.get_backward_time()+transfer.get_transfer_time())
            async def run():
                if mode == 'cancel-and-restart':
                    task = asyncio.create_task(compute.emulate_compute())
                    await asyncio.sleep(0)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    else:
                        raise RuntimeError('Timing workload did not observe cancellation')
                await transfer.emulate_transfer()
                await compute.emulate_compute()
            start = time.monotonic()
            if mode == 'sync':
                transfer.emulate_transfer(); compute.emulate_compute()
            else:
                asyncio.run(run())
            elapsed = time.monotonic()-start
            memory_ok = memory.check_memory() is True
            limited = module.GPUMemoryEmulator(gpu_cap=float(memory.get_peak_memory())/2,
                gpu=gpu, model='resnet18', batch_size=1, database_path=str(database), num_gpus=1)
            try:
                limited.check_memory()
            except Exception as error:
                memory_rejected = 'Peak memory usage exceeds GPU capacity' in str(error)
            else:
                memory_rejected = False
            cases.append({'profile': gpu, 'mode': mode, 'expected_seconds': expected,
                          'elapsed_seconds': elapsed,
                          'memory_cap_rejected': memory_rejected,
                          'passed': elapsed >= expected*.95 and memory_ok and memory_rejected})
        paths = [database/'time/compute'/part/gpu/'resnet18/time_by_batch_size.csv' for part in ('forward','backward')]
        paths += [database/'time/transfer'/gpu/'resnet18'/name for name in
                  ('data_transfer_time_by_batch_size.csv','model_transfer_time.txt')]
        paths += [database/'memory'/gpu/'resnet18/memory.csv']
        for path in paths:
            hashes[str(path.relative_to(args.repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    # Exercise the unprofiled-batch fallback without loading a pickle. This
    # pinned revision references joblib without importing it.
    interpolation_error = None
    try:
        module.ComputeTimeEmulator(gpu=profiles[0], model='resnet18', batch_size=1000000,
            from_databse=True, database_path=str(database))
    except NameError as error:
        interpolation_error = error.name
    report = {'schema': 1, 'unprofiled_batch_missing_import': interpolation_error, 'upstream_revision': revision, 'pandas_version': pandas.__version__,
        'profiles': profiles, 'cases': cases, 'passed': all(c['passed'] for c in cases),
        'upstream_input_sha256': hashes,
        'experiment_sha256': hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        'nvidia_recovery_verified': False, 'pcie_or_firmware_emulated': False,
        'custom_pytorch_or_kubernetes_deployed': False,
        'limitations': ['Workload timing/cancellation only; cancellation is injected by this harness',
                       'No numerical training, CUDA, PCIe, firmware or electrical reset',
                       'Single-GPU batch-size-1 CSV rows; no interpolation, scaling or profile accuracy validation']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','upstream_input_sha256')},indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__': main()

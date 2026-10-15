#!/usr/bin/env python3
"""Read selected SANV declaration gates; never executes AML or writes memory."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import firmware_runtime as f


def main():
    if os.geteuid():raise RuntimeError('Reading reserved firmware NVS requires root')
    from gpu_recovery import trusted_file
    tool=shutil.which('iasl')
    if not tool:raise RuntimeError('iasl required')
    executable=str(trusted_file(tool))
    table=pathlib.Path('/sys/firmware/acpi/tables/DSDT').read_bytes()
    with tempfile.TemporaryDirectory(prefix='d3-gate-') as directory:
        aml=pathlib.Path(directory)/'dsdt.aml';aml.write_bytes(table)
        result=subprocess.run([executable,'-d',str(aml)],capture_output=True,text=True,timeout=60)
        if result.returncode:raise RuntimeError('Current DSDT disassembly failed')
        base,size,fields=f.literal_named_nvs_definition(aml.with_suffix('.dsl').read_text(),'SANV')
    if not f.nvs_resource(base,size,pathlib.Path('/proc/iomem').read_text()):
        raise RuntimeError('SANV is not inside reserved ACPI Non-volatile Storage')
    names=['PFG'+str(i) for i in range(4)]
    if any(name not in fields or fields[name][1]!=8 for name in names):
        raise RuntimeError('Expected declaration gates not present')
    descriptor=os.open('/dev/mem',os.O_RDONLY|os.O_CLOEXEC)
    try:data=os.pread(descriptor,size,base)
    finally:os.close(descriptor)
    if len(data)!=size:raise RuntimeError('Short reserved-memory read')
    integer=int.from_bytes(data,'little')
    selected={name:{'value':(integer>>fields[name][0])&255,'bit_offset':fields[name][0]} for name in names}
    print(json.dumps({'kind':'Read-only current SANV gate snapshot','dsdt_sha256':hashlib.sha256(table).hexdigest(),
                      'address':hex(base),'size':size,'fields':selected,
                      'limitations':'Current NVS bytes do not prove the value copied during earlier AML namespace initialization. PFG-to-port relationships and reset implementation require separate review. No reset authorized.'},indent=2))


if __name__=='__main__':main()

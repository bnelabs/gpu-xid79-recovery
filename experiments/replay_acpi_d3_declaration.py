#!/usr/bin/env python3
"""Run an extracted reset _DSD method in an offline ACPICA fixture."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_recovery as g


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dsl',type=pathlib.Path)
    parser.add_argument('--scope',required=True)
    parser.add_argument('--output',type=pathlib.Path)
    args=parser.parse_args()
    data=args.dsl.read_bytes()
    methods=[o for o in g.asl_reset_objects(data.decode(),True)
             if g.acpi_path(o['path'])==g.acpi_path(args.scope)+('_DSD',)
             and o['kind']=='Method' and 'FundamentalDeviceResetTriggeredOnD3ToD0' in o['source']]
    if len(methods)!=1 or methods[0]['truncated']:raise RuntimeError('One complete matching _DSD method required')
    method=methods[0]['source']
    # Only this reviewed dependency pattern is admitted. No host tables, region
    # handlers, PCI access, or original namespace initialization are loaded.
    if any(word in method for word in ('OperationRegion','Field (','Store (','Sleep (','While (')):
        raise RuntimeError('Method outside the reviewed declaration-only fixture')
    results=[]
    with tempfile.TemporaryDirectory(prefix='acpi-d3-declaration-') as directory:
        base=pathlib.Path(directory)
        for gate in (0,1):
            for helper in (False,True):
                asl='DefinitionBlock ("", "DSDT", 2, "LAB", "D3RESET", 1) {\n'
                asl+=f'Name (PDSD, {gate})\n'
                if helper:asl+='Method (PINI, 0) { Return (Zero) }\n'
                asl+='If (PDSD == One) {\n'+method+'\n}\n}'
                source=base/'fixture.asl';source.write_text(asl)
                compile_result=subprocess.run(['iasl','-p',str(base/'fixture'),str(source)],capture_output=True,text=True,timeout=15)
                if compile_result.returncode:raise RuntimeError(compile_result.stdout+compile_result.stderr)
                result=subprocess.run(['acpiexec','-di','-b','evaluate \\_DSD',str(base/'fixture.aml')],capture_output=True,text=True,timeout=15)
                log=result.stdout+result.stderr
                available='returned object' in log and 'AE_NOT_FOUND' not in log
                if available != bool(gate):raise RuntimeError(log)
                expected='Contains 6 Elements' if helper else 'Contains 4 Elements'
                if gate and expected not in log:raise RuntimeError(log)
                results.append({'gate':gate,'modeled_PINI_present':helper,'method_available':available,'expected_branch_confirmed':True})
    report={'kind':'Extracted _DSD method under modeled namespace gates; offline ACPICA, not physical reset',
            'source_sha256':hashlib.sha256(data).hexdigest(),'method_sha256':hashlib.sha256(method.encode()).hexdigest(),
            'scope':args.scope,'cases':results,'hardware_recovery_verified':False,
            'limitations':'PDSD gate and optional PINI are modeled. No complete firmware namespace, NVS, hardware, _PS0/_PS3 or reset implementation is executed. Input is executable AML; use only reviewed input.'}
    output=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(output)
    print(output,end='')


if __name__=='__main__':main()

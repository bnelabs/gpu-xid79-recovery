#!/usr/bin/env python3
"""Replay pinned NVIDIA reset methods with modeled transports, never hardware.

This is source-level fault injection, not an NVIDIA GPU/firmware emulator.
No vendor module is imported; only hash-checked method ASTs are compiled.
"""
import argparse,ast,hashlib,json,pathlib,types

COMMIT='44f261a7ebff96559488230b420e4a3035b30d58'
METHODS={
 ('NvidiaDevice','set_next_sbr_to_fundamental_reset'):'df388454fb67524f2f2540d91a8c789495ba492579e71d20439c0753e38ee50c',
 ('NvidiaDevice','reset_with_flr'):'707cb0ebbe2b4967db0df1355753b57a372d45ca9b6be1a56f30bafcd315d436',
 ('BrokenGpu','reset_with_sbr'):'da7058ff4a16e36ee687378f117fa13a15916186737c0b127a3b0f496262f423',
 ('FspRpc','prc_couple_reset'):'785b6674600fde3cc506492fb400a08b574c37d7f369700aa69203eedf57dcee'}

class StepBudgetExceeded(RuntimeError):pass

class Clock:
 def __init__(self):self.sleeps=0
 def sleep(self,seconds):
  self.sleeps+=1
  if self.sleeps>20:raise StepBudgetExceeded('Vendor loop exceeded simulated step budget')

class Model:
 def __init__(self,config='ready',bridge_recovers=True,hopper=False,permission=1,armed=0):
  self.events=[];self.config_mode=config;self.bridge_recovers=bridge_recovers
  self.is_reset_coupling_supported=hopper;self.is_hopper=hopper;self.is_blackwell_plus=False
  self.config=types.SimpleNamespace(read32=self.read_config);self.devctl={}
  self.parent=types.SimpleNamespace(is_bridge=lambda:True,toggle_sbr=self.sbr)
  self.knobs={35:permission,36:armed};self.reply=[];self.clock=Clock()
  self.fsp_rpc=types.SimpleNamespace(prc_knob_read=self.knob_read,prc_knob_write=self.knob_write)
  self.fsp_rpc_mods=types.SimpleNamespace(prc_cmd=self.prc_cmd)
 def reset_pre(self,**kwargs):self.events.append(['reset_pre',kwargs])
 def reset_post(self):self.events.append(['reset_post'])
 def is_flr_supported(self):return True
 def _restore_cfg_space(self):self.events.append(['restore_config'])
 def set_command_memory(self,value):self.events.append(['memory_enable',value])
 def sanity_check(self):return self.config_mode=='ready'
 def sanity_check_cfg_space(self):return self.config_mode=='ready'
 def read_config(self,offset):
  if self.config_mode=='crs_then_ready' and self.clock.sleeps>=4:self.config_mode='ready'
  return {'ready':0x233110de,'crs_then_ready':0xffff0001,'crs_forever':0xffff0001,'lost':0xffffffff}[self.config_mode]
 def sbr(self):
  self.events.append(['upstream_sbr'])
  if self.bridge_recovers:self.config_mode='ready'
  return self.bridge_recovers
 def _init_fsp_rpc(self):
  self.events.append(['init_fsp_rpc'])
  if self.config_mode=='lost':raise ConnectionError('Modeled in-band FSP transport unreachable')
 def knob_read(self,key):self.events.append(['knob_read',key]);return self.knobs[key]
 def knob_write(self,key,value):self.events.append(['knob_write',key,value]);self.knobs[key]=value
 def read_bad_ok(self,offset):self.events.append(['mmio_read',offset]);return 0
 def prc_cmd(self,words):self.events.append(['fsp_command',words]);return self.reply


def load_methods(path,clock):
 source=path.read_text();tree=ast.parse(source);result={}
 gpu=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Gpu')
 gates=[node for node in ast.walk(gpu) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Attribute) and t.attr=='is_reset_coupling_supported' for t in node.targets)]
 if len(gates)!=1 or ast.dump(gates[0].value)!=ast.dump(ast.parse('self.is_hopper',mode='eval').body):
  raise ValueError('Unsupported reset-coupling capability gate')
 for (owner,name),digest in METHODS.items():
  cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name==owner)
  method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==name)
  if hashlib.sha256(ast.get_source_segment(source,method).encode()).hexdigest()!=digest:
   raise ValueError('Vendor method hash mismatch: '+owner+'.'+name)
  namespace={'__builtins__':{'len':len,'hex':hex},'time':clock,'GpuError':RuntimeError,
             'debug':lambda *a,**k:None,'error':lambda *a,**k:None,
             'PrcKnob':types.SimpleNamespace(PRC_KNOB_ID_FORCE_RESET_COUPLING=types.SimpleNamespace(value=36),
                         PRC_KNOB_ID_FORCE_RESET_COUPLING_ALLOW_INB=types.SimpleNamespace(value=35))}
  exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec',optimize=0),namespace)
  result[(owner,name)]=namespace[name]
 return result


def replay(path):
 cases=[
  ('flr_ready','flr',{},True,None),
  ('flr_transient_crs','flr',{'config':'crs_then_ready'},True,None),
  ('flr_lost_link','flr',{'config':'lost'},False,None),
  ('flr_permanent_crs','flr',{'config':'crs_forever'},None,'StepBudgetExceeded'),
  ('sbr_restores_link','sbr',{'config':'lost'},True,None),
  ('sbr_cannot_restore_link','sbr',{'config':'lost','bridge_recovers':False},False,None),
  ('hopper_coupling_permitted','couple',{'hopper':True},True,None),
  ('hopper_coupling_denied','couple',{'hopper':True,'permission':0},False,None),
  ('hopper_coupling_already_enabled','couple',{'hopper':True,'permission':0,'armed':1},True,None),
  ('hopper_cannot_arm_after_link_loss','couple',{'hopper':True,'config':'lost'},None,'ConnectionError'),
  ('non_hopper_coupling_rejected','couple',{},None,'AssertionError'),
  ('coupling_reply_rejected','couple_bad_reply',{'hopper':True},None,'RuntimeError')]
 output=[]
 for name,operation,settings,expected,expected_error in cases:
  model=Model(**settings);methods=load_methods(path,model.clock)
  model.fsp_rpc_mods.prc_couple_reset=types.MethodType(methods[('FspRpc','prc_couple_reset')],model.fsp_rpc_mods)
  if operation=='couple_bad_reply':model.reply=[1]
  key={'flr':('NvidiaDevice','reset_with_flr'),'sbr':('BrokenGpu','reset_with_sbr')}.get(operation,('NvidiaDevice','set_next_sbr_to_fundamental_reset'))
  result=None;error=None
  try:result=methods[key](model)
  except (StepBudgetExceeded,ConnectionError,AssertionError,RuntimeError) as exc:error=type(exc).__name__
  passed=result==expected and error==expected_error
  # Permission refusal and non-Hopper rejection must send no reset command.
  if name in ('hopper_coupling_denied','non_hopper_coupling_rejected'):
   passed=passed and not any(event[0] in ('fsp_command','knob_write') for event in model.events)
  if name=='hopper_coupling_permitted':passed=passed and ['fsp_command',[0x10104]] in model.events
  output.append({'case':name,'matched_expected':passed,'returned':result,'exception':error,
                 'simulated_sleeps':model.clock.sleeps,'events':model.events})
 return {'kind':'Source-level fault injection; no physical GPU or firmware execution',
         'upstream':'https://github.com/NVIDIA/gpu-admin-tools','reviewed_commit':COMMIT,
         'method_sha256':{owner+'.'+name:digest for (owner,name),digest in METHODS.items()},
         'cases':output,'passed':sum(item['matched_expected'] for item in output),
         'limitations':['Transport responses, reset effect and firmware replies are modeled inputs',
                        'No hardware recovery, physical reset propagation, PCI timing or CUDA behavior is validated',
                        'Step-budget failure identifies an unbounded source loop under modeled perpetual CRS, not a measured hardware hang']}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('upstream',type=pathlib.Path)
 args=parser.parse_args();report=replay(args.upstream/'nvidia_gpu_tools.py')
 print(json.dumps(report,indent=2))
 if report['passed']!=len(report['cases']):raise SystemExit(1)

if __name__=='__main__':main()

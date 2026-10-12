"""Shadow-only client. Rules remain authoritative; no tool execution here."""
import json,os,time,urllib.request
from pathlib import Path
from server.ep_decision_core.common import digest
from server.ep_decision_core.clef_choice_adapter import ClefChoiceAdapter,assess_choice

def _call(path,payload=None,timeout=2):
 from config.settings import EP_CLEF_BRIDGE_URL,EP_CLEF_TOKEN_PATH
 token=Path(EP_CLEF_TOKEN_PATH).read_text().strip()
 if len(token)!=64:raise ValueError('bridge_token_configuration')
 wire=json.dumps(payload,allow_nan=False).encode() if payload is not None else None
 req=urllib.request.Request(EP_CLEF_BRIDGE_URL+path,data=wire,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as r:
  data=r.read(1048577)
  if len(data)>1048576:raise ValueError('bridge_response_size')
  return json.loads(data)

def health(selection=None):
 result=_call('/health')
 if selection and (result.get('epoch')!=selection['epoch'] or result.get('identity')!=selection['identity'] or not result.get('ready')):raise ValueError('bridge_not_bound_ready')
 return result

def evaluate(request,selection,current_state):
 def predict(record):
  return _call('/predict',{'record':record,**selection},timeout=32)
 actor={k:request[k] for k in ('tenant','owner','acl_version')}
 result=assess_choice(ClefChoiceAdapter(predict,selection['identity']),request,actor=actor,now=time.time(),current_state=current_state)
 env=result.get('envelope') or {}
 raw=env.get('raw_native_wire') or {}
 return {'status':'PASS_SHADOW' if not result['fallback'] and result['action_ready'] else 'FALLBACK_RULES',
         'reason':result.get('reason'),'model_called':env.get('model_called',None),
         'operation_id':env.get('operation_id'),'native_hash':env.get('native_sha256'),
         'action_id':(env.get('normalized') or {}).get('action_id'),
         'input_tokens':raw.get('input_tokens'),'wall_ms':(env.get('normalized') or {}).get('elapsed_ms'),
         'guard':result.get('guard'),'quality_eligible':False,'execution_authorized':False}

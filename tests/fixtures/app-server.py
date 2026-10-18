#!/usr/bin/env python3
"""No-network fixture for the public app-server wire protocol."""
import json, os, sys, time, tomllib
from pathlib import Path
root = Path(__file__).parent
options = json.loads((root / 'options.json').read_text())
def log(value):
    with (root / 'requests.jsonl').open('a') as f: f.write(json.dumps(value) + '\n')
def send(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)
if '--version' in sys.argv:
    print('codex-cli ' + options.get('version','0.157.1'))
    sys.exit(0)
if 'login' in sys.argv:
    print('Logged in using ChatGPT' if not options.get('apiKey') else 'Logged in using API key')
    sys.exit(0)
if any(key in os.environ for key in ['OPENAI_API_KEY', 'APP_SECRET', 'CODEX_API_KEY']): sys.exit(9)
log({'spawn': os.getpid(), 'cwdEmpty': not any(Path.cwd().iterdir())})
config = {'mcp_servers': {'inherited': {'enabled': True}}, 'features': {}, 'model_providers': {}, 'model': 'quality-model', 'model_reasoning_effort':'xhigh'}
def merge(target, source):
    for key, value in source.items():
        if isinstance(value,dict): merge(target.setdefault(key,{}),value)
        else: target[key] = value
for i,arg in enumerate(sys.argv):
    if arg == '-c': merge(config,tomllib.loads(sys.argv[i+1]))
if options.get('unsafeConfig'): config['features']['shell_tool'] = True
thread = None
turns = 0
threads = 0
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get('method'), request.get('params',{})
    if 'id' not in request: continue
    log({'method':method,'params':params,'pid':os.getpid()})
    if options.get('phaseDelays'): time.sleep({'account/read':.03, 'configRequirements/read':.04, 'config/read':.05}.get(method, 0))
    def reply(value): send({'id':request['id'],'result':value})
    def event(method,params): send({'method':method,'params':params})
    if method == 'initialize': reply({'userAgent':'fixture'})
    elif method == 'config/read':
        if json.loads((root / 'options.json').read_text()).get('unsafeDynamic'): config['features']['shell_tool']=True
        reply({'config':config,'origins':{},'layers':None})
    elif method == 'configRequirements/read': reply({'requirements':{'featureRequirements':{'shell_tool':True}} if options.get('unsafeRequirements') else None})
    elif method == 'account/read': reply({'account': {'type':'apiKey' if options.get('apiKeyAccount') else 'chatgpt'},'requiresOpenaiAuth':True,'workspaceRouting':None})
    elif method == 'model/list':
        reply({'data':[
            {'model':'catalog-default','isDefault':True,'defaultReasoningEffort':'medium'},
            {'model':'selected-quality-model','isDefault':False,'defaultReasoningEffort':'high'},
            {'model':'quality-model','isDefault':False,'defaultReasoningEffort':'medium'},
        ],'nextCursor':None})
    elif method == 'thread/start':
        if options.get('phaseDelays'): time.sleep(.06)
        assert params['ephemeral'] is True and params['sandbox']=='read-only' and params['approvalPolicy']=='never'
        assert params['dynamicTools']==[] and params['environments']==[]
        assert config['mcp_servers']['inherited']['enabled'] is False
        threads += 1
        thread = 'thread-'+str(os.getpid())+'-'+str(threads)
        reply({'thread':{'id':thread},'model': params.get('model') or 'quality-model','reasoningEffort':params.get('config',{}).get('model_reasoning_effort',config['model_reasoning_effort']),'modelProvider':'openai','cwd':os.getcwd(),'sandbox':{'type':'readOnly','networkAccess':False},'approvalPolicy':'never','instructionSources':[]})
    elif method == 'thread/unsubscribe':
        if options.get('phaseDelays'): time.sleep(.05)
        assert params['threadId']==thread
        event('deprecationNotice',{'summary':'fixture','details':None})
        event('warning',{'threadId':thread,'message':'fixture'})
        event('thread/closed',{'threadId':thread})
        reply({'status':'unsubscribed'})
        thread=None
    elif method == 'turn/start':
        if options.get('phaseDelays'): time.sleep(.05)
        turns += 1
        turn = 'turn-'+str(turns)
        prompt=params['input'][0]['text']
        assert params['threadId']==thread
        reply({'turn':{'id':turn,'status':'inProgress','items':[]}})
        def emit(kind,**extra): event(kind,{'threadId':thread,'turnId':turn,**extra})
        if 'BEFORE_START' in prompt:
            emit('item/agentMessage/delta',delta='{"type":"text","text":"Unidentified."}\n');time.sleep(60)
        if 'STALL' in prompt: time.sleep(60)
        if 'MALFORMED_PROTOCOL' in prompt:
            print('{not json}',flush=True);time.sleep(60)
        if 'PROTOCOL_LIMIT' in prompt:
            print('x' * (2 * 1024 * 1024 + 1),flush=True);time.sleep(60)
        if 'STALE_ID' in prompt:
            turn='wrong-turn'
        if 'NATIVE_ITEM' in prompt:
            emit('item/started',item={'type':'commandExecution','id':'native'});time.sleep(60)
        if 'APPROVAL' in prompt:
            send({'id':'approval','method':'item/commandExecution/requestApproval','params':{}})
            time.sleep(60)
        phase = 'commentary' if 'COMMENTARY' in prompt else None if 'NULL_PHASE' in prompt else 'final_answer'
        emit('item/started',item={'type':'agentMessage','id':'answer','text':'','phase':phase})
        if options.get('phaseDelays'): time.sleep(.08)
        if 'TOOLS' in prompt:
            output='{"type":"tools","toolCalls":[{"name":"lookup","argumentsJson":"{}"}]}\n{"type":"done"}\n'
        else:
            output='{"type":"text","text":"First sentence. "}\n'
            if 'SPLIT_RECORD' in prompt:
                emit('item/agentMessage/delta',itemId='answer',delta=output[:12])
                time.sleep(.12)
                emit('item/agentMessage/delta',itemId='answer',delta=output[12:])
            else:
                emit('item/agentMessage/delta',itemId='answer',delta=output)
            time.sleep(.2)
            if 'MIXED' in prompt: output += '{"type":"tools","toolCalls":[{"name":"lookup","argumentsJson":"{}"}]}\n'
            elif 'MALFORMED' in prompt: output += '{bad}\n'
            else: output += '{"type":"text","text":"Second sentence."}\n{"type":"done"}\n'
            emit('item/agentMessage/delta',itemId='answer',delta=output[len('{"type":"text","text":"First sentence. "}\n'):])
        if 'TOOLS' in prompt: emit('item/agentMessage/delta',itemId='answer',delta=output)
        emit('item/completed',item={'type':'agentMessage','id':'answer','text': output+'mismatch' if 'MISMATCH' in prompt else output,'phase':phase})
        emit('thread/tokenUsage/updated',tokenUsage={'last':{'inputTokens':10,'outputTokens':5,'totalTokens':15,'cachedInputTokens':0,'cacheWriteInputTokens':0,'reasoningOutputTokens':0},'total':{},'modelContextWindow':None})
        time.sleep(.2)
        event('turn/completed',{'threadId':thread,'turn':{'id':turn,'items':[],'status':'failed' if 'FAIL' in prompt else 'completed','error':{'message':'private provider details'} if 'FAIL' in prompt else None}})
    else:
        send({'id':request['id'],'error':{'message':'unexpected fixture method'}})

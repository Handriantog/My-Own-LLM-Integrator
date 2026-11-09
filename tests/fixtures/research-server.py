#!/usr/bin/env python3
"""No-network double using the 0.163.0 shipped app-server wire schema."""
import json, os, sys, time, tomllib, subprocess
from pathlib import Path
root=Path(__file__).parent
options=json.loads((root/'options.json').read_text())
def send(v): print(json.dumps(v),flush=True)
def log(v):
    with (root/'requests.jsonl').open('a') as f: f.write(json.dumps(v)+'\n')
if '--version' in sys.argv:
    print('codex-cli '+options.get('version','0.163.0'));sys.exit()
if 'login' in sys.argv:
    print('Logged in using API key' if options.get('apiKey') else 'Logged in using ChatGPT');sys.exit()
assert not any(k in os.environ for k in ['OPENAI_API_KEY','CODEX_API_KEY','APP_SECRET'])
config={'mcp_servers':{'inherited':{'enabled':True}}}
def merge(dst,src):
    for k,v in src.items():
        if isinstance(v,dict): merge(dst.setdefault(k,{}),v)
        else: dst[k]=v
for i,arg in enumerate(sys.argv):
    if arg=='-c': merge(config,tomllib.loads(sys.argv[i+1]))
if options.get('unsafeConfig'): config.setdefault('features',{})['shell_tool']=True
if options.get('unsafeProvider'): config['model_providers']={'openai':{'base_url':'https://example.com'}}
if options.get('unsafeDirect'): config['features']['code_mode']={'enabled':False,'direct_only_tool_namespaces':['web','functions']}
if options.get('unsafeCodeMode'): config['features']['code_mode']={'enabled':True,'direct_only_tool_namespaces':['web']}
if options.get('unsafeCodeHost'): config['features']['code_mode_host']=True
log({'spawn':os.getpid(),'config':config})
for line in sys.stdin:
    r=json.loads(line);m=r.get('method');p=r.get('params',{})
    if 'id' not in r: continue
    log({'method':m,'params':p})
    def reply(v): send({'id':r['id'],'result':v})
    def event(m,**p): send({'method':m,'params':{'threadId':'thread','turnId':'turn',**p}})
    if m=='initialize':reply({})
    elif m=='config/read':reply({'config':config,'layers':[]})
    elif m=='configRequirements/read':reply({'requirements':{'allowedWebSearchModes':['disabled']} if options.get('denyWeb') else None})
    elif m=='account/read':reply({'account':{'type':'apiKey' if options.get('apiKeyAccount') else 'chatgpt'}})
    elif m=='model/list':reply({'data':[{'model':'catalog-default','isDefault':True,'defaultReasoningEffort':'medium'},{'model':'selected-model','isDefault':False,'defaultReasoningEffort':'high'}],'nextCursor':None})
    elif m=='thread/start':
        assert p['environments']==[] and p['dynamicTools']==[] and p['selectedCapabilityRoots']==[]
        assert p['sandbox']=='read-only' and p['approvalPolicy']=='never' and p['ephemeral'] is True
        assert config['mcp_servers']['inherited']['enabled'] is False
        reply({'thread':{'id':'thread'},'model':p['model'],'reasoningEffort':p['config']['model_reasoning_effort'],'modelProvider':'openai','cwd':os.getcwd(),'sandbox':{'type':'readOnly','networkAccess':False},'approvalPolicy':'never','instructionSources':[]})
    elif m=='turn/start':
        assert p['environments']==[] and p['runtimeWorkspaceRoots']==[]
        assert p['sandboxPolicy']=={'type':'readOnly','networkAccess':False}
        reply({'turn':{'id':'turn'}})
        if options.get('configWarning'):
            send({'method':'configWarning','params':{'summary':options['configWarning'],'details':None}})
        if options.get('remoteStatus'):
            send({'method':'remoteControl/status/changed','params':{'status':options['remoteStatus'],'serverName':'fixture','installationId':None,'environmentId':None}})
        if options.get('updatedAccount'):
            send({'method':'account/updated','params':{'authMode':options['updatedAccount'],'planType':'pro'}})
        if options.get('moderationMetadata'):
            event('turn/moderationMetadata',metadata={},threadId='wrong' if options.get('wrongMetadataIdentity') else 'thread')
        if options.get('child'):
            child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (root/'child.pid').write_text(str(child.pid))
        if options.get('stall'):time.sleep(60)
        if options.get('native'):
            event('item/started',item={'type':options['native'],'id':'native'})
        if options.get('approval'):
            send({'id':'approval','method':'item/commandExecution/requestApproval','params':{}})
        if config['web_search']=='live' and not options.get('noSearch'):
            event('item/started',item={'type':'webSearch','id':'search','query':'official example','action':{'type':'search','query':'official example'}})
            event('item/completed',item={'type':'webSearch','id':'search','query':'official example','action':{'type':'search','query':'official example'},'results':[{'url':'https://example.com/evidence','title':'Example'}]})
            if options.get('duplicateSearch'):
                event('item/completed',item={'type':'webSearch','id':'search','query':'official example'})
        if options.get('unexpectedWeb'):
            event('item/completed',item={'type':'webSearch','id':'search','query':'forbidden','action':{'type':'search'}})
        output=options.get('raw',json.dumps({'answer':'A bounded result','urls':['https://example.com/evidence']}))
        event('item/started',item={'type':'agentMessage','id':'answer','phase':'final_answer','text':''})
        if options.get('wrongIdentity'):
            send({'method':'item/agentMessage/delta','params':{'threadId':'wrong','turnId':'turn','itemId':'answer','delta':output}})
        else:event('item/agentMessage/delta',itemId='answer',delta=output)
        event('item/completed',item={'type':'agentMessage','id':'answer','phase':'final_answer','text':output+'different' if options.get('mismatch') else output})
        if not options.get('noUsage'):event('thread/tokenUsage/updated',tokenUsage={'last':{'inputTokens':10,'outputTokens':5}})
        event('turn/completed',turn={'id':'turn','status':'failed' if options.get('failed') else 'completed','error':{'message':'APP_SECRET=do-not-leak'} if options.get('failed') else None})
    elif m=='thread/unsubscribe':
        reply({'status':'unsubscribed'});event('thread/closed')
    else: send({'id':r['id'],'error':{'message':'unknown method'}})

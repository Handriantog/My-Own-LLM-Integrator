"""One restricted research call; no network listener or general agent execution."""
import json, os, signal, sys, threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from bridge import stop, watch_parent
from myown_llm_integrator.research import run_research
from myown_llm_integrator.errors import ProviderError
from myown_llm_integrator.provider_session import decode

def main():
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    threading.Thread(target=watch_parent,args=(int(sys.argv[1]) if len(sys.argv)>1 else os.getppid(),),daemon=True).start()
    try:
        raw=sys.stdin.buffer.read(192*1024+1)
        if len(raw)>192*1024: raise ProviderError('Research request exceeded the size limit.')
        request=decode(raw.decode('utf-8'))
        result=run_research(request)
        print(json.dumps({'ok':True,'value':result}),flush=True)
        return 0
    except ProviderError as error:
        print(json.dumps({'ok':False,'error':str(error)}),flush=True)
    except Exception:
        print(json.dumps({'ok':False,'error':'Restricted research failed; no retry or API fallback was used.'}),flush=True)
    return 1
if __name__=='__main__': sys.exit(main())

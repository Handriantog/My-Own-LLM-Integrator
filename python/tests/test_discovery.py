import pytest


def test_codex_model_inventory_uses_only_bounded_metadata_rpc(tmp_path):
    import json
    import sys
    from myown_llm_integrator.discovery import codex_catalog as _codex_catalog
    log=tmp_path/'rpc.jsonl'
    binary=tmp_path/'codex'
    binary.write_text(f'''#!{sys.executable}
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    with open({str(log)!r},'a') as f:f.write(json.dumps(request)+'\\n')
    if 'id' in request:
        # Codex 0.157.1's generated ClientRequest schema requires null/omitted
        # params here. Reject malformed requests like the real server does.
        if request['method']=='account/gatewayOAuth/read' and request.get('params') is not None:
            print(json.dumps({{'id':request['id'],'error':{{'code':-32600,'message':'Invalid request: invalid type: map, expected unit'}}}}),flush=True)
            continue
        result={{'data':[{{'model':'available-model','displayName':'Available'}}]}} if request['method']=='model/list' else {{}}
        print(json.dumps({{'id':request['id'],'result':result}}),flush=True)
''')
    binary.chmod(0o700)
    assert _codex_catalog(str(binary),timeout=1)==[{'id':'available-model','name':'Available','source':'discovered'}]
    methods=[json.loads(line)['method'] for line in log.read_text().splitlines()]
    assert methods==['initialize','initialized','account/gatewayOAuth/read','model/list']


def test_codex_model_inventory_timeout_reaps_child(tmp_path):
    import sys
    import time
    from myown_llm_integrator.discovery import codex_catalog as _codex_catalog
    binary=tmp_path/'codex'
    binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
    binary.chmod(0o700)
    started=time.monotonic()
    with pytest.raises(TimeoutError):
        _codex_catalog(str(binary),timeout=.1)
    assert time.monotonic()-started<2

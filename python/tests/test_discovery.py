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


def test_codex_cli_catalog_stops_excessive_output(tmp_path):
    import sys
    import time
    from myown_llm_integrator.discovery import codex_cli_catalog
    binary = tmp_path / 'codex'
    binary.write_text(f'#!{sys.executable}\nimport sys, time\n'
                      'sys.stdout.write("x" * 3_000_000)\n'
                      'sys.stdout.flush()\ntime.sleep(30)\n')
    binary.chmod(0o700)
    started = time.monotonic()
    with pytest.raises(ValueError):
        codex_cli_catalog(str(binary))
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('mode', ['rpc', 'cli'])
def test_model_discovery_cleans_up_descendants(tmp_path, mode):
    import os
    import signal
    import sys
    import time
    from myown_llm_integrator.discovery import codex_catalog, codex_cli_catalog
    pid_path = tmp_path / 'descendant.pid'
    binary = tmp_path / 'codex'
    binary.write_text(f'''#!{sys.executable}
import json, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))
if 'debug' in sys.argv:
    print('{{"models":[]}}')
else:
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' in request:
            print(json.dumps({{'id': request['id'], 'result': {{'data': []}}}}), flush=True)
''')
    binary.chmod(0o700)
    discover = codex_catalog if mode == 'rpc' else codex_cli_catalog
    try:
        assert discover(str(binary)) == []
        pid = int(pid_path.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.02)
        pytest.fail('discovery left a background child running')
    finally:
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

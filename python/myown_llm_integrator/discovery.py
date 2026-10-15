"""Bounded, metadata-only Codex model discovery. Never starts inference."""
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from .errors import ProviderError
from .process import _environment, _run

def codex_catalog(binary, timeout=6):
    """Read model/list only. Never create a thread, turn or inference request."""
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix='research-models-') as directory:
        process = subprocess.Popen([binary, '--no-daemon', '-c', 'features.plugins=false',
            '-c', 'features.apps=false', '-c', 'model_provider="openai"',
            'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=directory, env=_environment(), start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b''
        def send(value):
            process.stdin.write(json.dumps(value).encode() + b'\n')
            process.stdin.flush()
        def receive(request_id):
            nonlocal buffer
            while time.monotonic() < deadline:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    if event.get('id') == request_id:
                        if 'error' in event:
                            raise ValueError('Model inventory is unavailable')
                        return event['result']
                if not selector.select(max(0, deadline-time.monotonic())):
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 2_000_000:
                    raise ValueError('Model inventory response too large')
            raise TimeoutError('Model inventory timed out')
        try:
            send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'myown_llm_integrator','version':'0.1.0'},
                'capabilities':{'experimentalApi':True, 'explicitGatewayOauth':True}}})
            receive(1)
            send({'method':'initialized','params':{}})
            send({'id':3,'method':'account/gatewayOAuth/read','params':None})
            receive(3)
            send({'id':2,'method':'model/list','params':{'limit':100,'includeHidden':False}})
            result = receive(2)
            return [{'id':m['model'], 'name':m.get('displayName') or m['model'], 'source':'discovered'}
                    for m in result.get('data', []) if isinstance(m.get('model'), str)]
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdin.close()
            process.stdout.close()


def codex_cli_catalog(binary):
    """Codex's metadata-only catalog command; may use a cached/bundled catalog.

    Keep this distinct from a verified account entitlement or inference probe.
    Older CLIs without this command leave discovery unavailable.
    """
    try:
        code, stdout, _ = _run([binary, '--no-daemon', 'debug', 'models'],
            timeout=5, cwd=os.getcwd(), env=_environment(), output_limit=2_000_000)
    except ProviderError:
        raise ValueError('Codex model catalog is unavailable') from None
    if code != 0:
        raise ValueError('Codex model catalog is unavailable')
    data = json.loads(stdout)
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ValueError('Unexpected Codex model catalog')
    return [{'id':model['slug'], 'name':model.get('display_name') or model['slug'], 'source':'catalog'}
        for model in data['models'] if isinstance(model, dict) and isinstance(model.get('slug'), str)
        and model['slug'] and model.get('visibility') == 'list']

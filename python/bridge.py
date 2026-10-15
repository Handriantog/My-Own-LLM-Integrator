"""One JSON request per process; used by the npm API, never a network server."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

# Works with `python -I` and a caller-owned interpreter; only this package is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from myown_llm_integrator import ProviderError, PROVIDERS, run_agent, validate_provider


def stop(signum, frame):
    raise ProviderError('Inference request cancelled.')


def watch_parent(pid):
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        time.sleep(.05)


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if len(sys.argv) > 1:
        threading.Thread(target=watch_parent, args=(int(sys.argv[1]),), daemon=True).start()
    try:
        request = json.load(sys.stdin)
        operation = request.get('operation', 'run')
        if operation == 'providers':
            response = [vars(p) for p in PROVIDERS.values()]
        elif operation == 'validate':
            response = vars(validate_provider(request.get('provider')))
        elif operation == 'run':
            if not isinstance(request.get('prompt'), str) or not isinstance(request.get('schema'), dict):
                raise ProviderError('Expected a prompt string and a JSON Schema object.')
            options = {'provider': request.get('provider')}
            if 'model' in request:
                model = request['model']
                if model is not None and (not isinstance(model, str) or not model.strip()):
                    raise ProviderError('Model must be a nonempty string or null.')
                options['model'] = model
            result, usage = run_agent(request['prompt'], request['schema'], **options)
            response = {'result': result, 'usage': usage}
        else:
            raise ProviderError('Unknown integrator operation.')
        print(json.dumps({'ok': True, 'value': response}), flush=True)
        return 0
    except ProviderError as error:
        print(json.dumps({'ok': False, 'error': str(error)}), flush=True)
        return 1
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Invalid inference request or unavailable runtime.'}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())

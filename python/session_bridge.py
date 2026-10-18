"""Private, persistent JSONL transport; each request uses the existing engine."""
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

# Support caller-owned interpreters with -I, without importing application code.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from myown_llm_integrator import ProviderError, run_agent


MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
GENERIC_ERROR = 'Invalid inference request or unavailable runtime.'
REQUIRED_FIELDS = {'id', 'operation', 'prompt', 'schema'}
OPTIONAL_FIELDS = {'provider', 'model'}


def stop(signum, frame):
    # BaseException unwinds the engine's finally blocks and cannot become an
    # ordinary request failure that leaves this cancelled session idle.
    raise SystemExit(128 + signum)


def watch_parent(pid):
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            os.kill(os.getpid(), signal.SIGTERM)
            return
        except PermissionError:
            pass
        time.sleep(.05)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field.')
        value[key] = item
    return value


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Nonfinite JSON number.')
    return number


def _invalid_constant(value):
    raise ValueError('Invalid JSON constant.')


def _identifier(request):
    value = request.get('id')
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return value if str(uuid.UUID(value)) == value.lower() else None
    except ValueError:
        return None


def _response(request):
    identifier = _identifier(request)
    try:
        if identifier is None:
            raise ProviderError('Request id must be a UUID string.')
        fields = request.keys()
        if not REQUIRED_FIELDS <= fields or fields - REQUIRED_FIELDS - OPTIONAL_FIELDS:
            raise ProviderError('Invalid inference request fields.')
        if request['operation'] != 'run':
            raise ProviderError('Unknown integrator operation.')
        if not isinstance(request['prompt'], str) or not isinstance(request['schema'], dict):
            raise ProviderError('Expected a prompt string and a JSON Schema object.')
        options = {}
        if 'provider' in request:
            provider = request['provider']
            if not isinstance(provider, str) or not provider.strip():
                raise ProviderError('Provider must be a nonempty string.')
            options['provider'] = provider
        if 'model' in request:
            model = request['model']
            if model is not None and (not isinstance(model, str) or not model.strip()):
                raise ProviderError('Model must be a nonempty string or null.')
            options['model'] = model
        started = time.monotonic()
        result, usage = run_agent(request['prompt'], request['schema'], **options)
        elapsed = (time.monotonic() - started) * 1000
        return {'id': identifier, 'ok': True, 'value': {'result': result, 'usage': usage},
                'inferenceMs': elapsed}
    except ProviderError as error:
        return {'id': identifier, 'ok': False, 'error': str(error)}
    except Exception:
        return {'id': identifier, 'ok': False, 'error': GENERIC_ERROR}


def _encode(response):
    # Finish and bound serialization before writing any part of a success frame.
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for part in encoder.iterencode(response):
        chunk = part.encode('utf-8')
        if len(output) + len(chunk) + 1 > MAX_OUTPUT:
            raise ValueError('Session response exceeded the output limit.')
        output.extend(chunk)
    output.extend(b'\n')
    return output


def _write(output, response):
    output.write(_encode(response))
    output.flush()


def _reply(output, request):
    response = _response(request)
    try:
        encoded = _encode(response)
    except Exception:
        encoded = _encode({'id': _identifier(request), 'ok': False, 'error': GENERIC_ERROR})
    output.write(encoded)
    output.flush()


def serve(input_stream, output_stream):
    _write(output_stream, {'type': 'ready', 'protocol': 1})
    while True:
        line = input_stream.readline(MAX_INPUT + 1)
        if not line:
            return 0
        try:
            if len(line) > MAX_INPUT:
                raise ValueError('Session request exceeded the input limit.')
            request = json.loads(line.decode('utf-8'), object_pairs_hook=_object,
                                 parse_float=_finite_float, parse_constant=_invalid_constant)
            if not isinstance(request, dict):
                raise ValueError('Expected a JSON object.')
        except Exception:
            _write(output_stream, {'id': None, 'ok': False, 'error': GENERIC_ERROR})
            return 1
        del line
        _reply(output_stream, request)
        # Do not retain the previous prompt or response while waiting for input.
        del request


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parent = int(sys.argv[1]) if len(sys.argv) > 1 else os.getppid()
    if parent <= 0:
        return 1
    threading.Thread(target=watch_parent, args=(parent,), daemon=True).start()
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    except (BrokenPipeError, OSError):
        return 1


if __name__ == '__main__':
    sys.exit(main())

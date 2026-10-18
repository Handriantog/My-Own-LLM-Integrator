"""Private acknowledged JSONL bridge for one call-scoped Codex provider."""
import os
from pathlib import Path
import signal
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_bridge import stop, watch_parent, _write, _identifier
from myown_llm_integrator.provider_session import ProviderSession, decode
from myown_llm_integrator.errors import ProviderError


def serve(input_stream, output_stream):
    session = None
    try:
        session = ProviderSession()
        _write(output_stream, {'type': 'ready', 'protocol': 3})
        while True:
            line = input_stream.readline(8 * 1024 * 1024 + 1)
            if not line:
                return 0
            request = decode(line.decode('utf-8'))
            identifier = _identifier(request) if isinstance(request, dict) else None
            if (len(line) > 8 * 1024 * 1024 or identifier is None
                or set(request) - {'id', 'operation', 'prompt', 'toolNames', 'model'}
                or request.get('operation') != 'runDecision' or not isinstance(request.get('prompt'), str)
                or not isinstance(request.get('toolNames'), list) or len(request['toolNames']) > 256
                or any(not isinstance(name, str) or not name or len(name) > 128 for name in request['toolNames'])
                or len(set(request['toolNames'])) != len(request['toolNames'])
                or ('model' in request and request['model'] is not None and
                    (not isinstance(request['model'], str) or not request['model'].strip()))):
                raise ProviderError('Invalid provider decision request.')
            started = time.monotonic()
            def accept(span):
                _write(output_stream, {'id': identifier, 'type': 'text', **span})
                # One unacknowledged record bounds callback backlog. Node cancellation
                # kills this bridge/process group even if its callback never settles.
                raw = input_stream.readline(4097)
                if len(raw) > 4096:
                    raise ProviderError('Invalid text acknowledgement.')
                ack = decode(raw.decode('utf-8'))
                if (not isinstance(ack, dict) or type(ack.get('sequence')) is not int
                    or ack != {'id': identifier, 'type': 'ack', 'sequence': span['sequence']}):
                    raise ProviderError('Invalid text acknowledgement.')
            try:
                result, usage, delivery = session.run(request, accept)
                _write(output_stream, {'id': identifier, 'ok': True, 'value': {'result': result, 'usage': usage},
                                      'inferenceMs': (time.monotonic() - started) * 1000, 'delivery': delivery,
                                      'phases': session.phases})
            except Exception as error:
                message = str(error) if isinstance(error, ProviderError) else 'Provider session failed; no retry or API fallback was used.'
                _write(output_stream, {'id': identifier, 'ok': False, 'error': message})
                return 1
            del request
    except Exception as error:
        message = str(error) if isinstance(error, ProviderError) else 'Cannot establish restricted provider session.'
        _write(output_stream, {'type': 'failure', 'error': message})
        return 1
    finally:
        if session is not None:
            session.close()


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    parent = int(sys.argv[1]) if len(sys.argv) > 1 else os.getppid()
    if parent <= 0: return 1
    threading.Thread(target=watch_parent, args=(parent,), daemon=True).start()
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == '__main__':
    sys.exit(main())

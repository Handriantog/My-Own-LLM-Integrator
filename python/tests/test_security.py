"""Security regressions using local fixtures, never authenticated providers."""
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from myown_llm_integrator import ProviderError, run_agent


def configure_provider(tmp_path, monkeypatch, provider, body):
    executable = tmp_path / 'provider'
    executable.write_text(f'#!{sys.executable}\n'
                          'import json, os, pathlib, subprocess, sys\n'
                          'if "login" in sys.argv:\n'
                          '    print("Logged in using ChatGPT")\n'
                          '    sys.exit(0)\n'
                          'sys.stdin.read()\n' + body)
    executable.chmod(0o700)
    monkeypatch.setenv('CODEX_BIN', str(executable))
    monkeypatch.setenv('AGENT_COMMAND_JSON', json.dumps([sys.executable, str(executable)]))
    for prefix in ('CODEX', 'AGENT'):
        monkeypatch.setenv(prefix + '_LOCK_PATH', str(tmp_path / (prefix + '.lock')))
        monkeypatch.setenv(prefix + '_TIMEOUT_SECONDS', '5')


def response_body(provider, result):
    payload = json.dumps(result)
    if provider == 'codex':
        return (f'pathlib.Path(sys.argv[sys.argv.index("-o") + 1]).write_text({payload!r})\n'
                'print(json.dumps({"type": "turn.completed", "usage": {}}))\n')
    return f'print({payload!r})\n'


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_provider_output_is_bounded_before_json_parsing(tmp_path, monkeypatch, stream):
    configure_provider(tmp_path, monkeypatch, 'pi',
                       f'sys.{stream}.buffer.write(b"x" * (33 * 1024 * 1024))\n'
                       'print("{}")\n')
    with pytest.raises(ProviderError, match='output limit'):
        run_agent('hello', {}, provider='pi')
    # An output-limit failure must release the request slot for the next caller.
    configure_provider(tmp_path, monkeypatch, 'pi', 'print("{}")\n')
    assert run_agent('hello', {}, provider='pi') == ({}, {})


def test_codex_response_file_is_bounded(tmp_path, monkeypatch):
    configure_provider(tmp_path, monkeypatch, 'codex',
                       'pathlib.Path(sys.argv[sys.argv.index("-o") + 1]).write_text('
                       'json.dumps({"answer": "x" * (33 * 1024 * 1024)}))\n'
                       'print(json.dumps({"type": "turn.completed", "usage": {}}))\n')
    with pytest.raises(ProviderError, match='output limit'):
        run_agent('hello', {}, provider='codex')


@pytest.mark.parametrize('provider', ['pi', 'codex'])
def test_schema_references_do_not_make_network_requests(tmp_path, monkeypatch, provider):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"type":"object"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configure_provider(tmp_path, monkeypatch, provider, response_body(provider, {}))
    try:
        url = f'http://127.0.0.1:{server.server_port}/private-schema'
        error = None
        try:
            run_agent('hello', {'$ref': url}, provider=provider)
        except ProviderError as caught:
            error = caught
        assert requests == [], 'schema validation fetched a remote resource'
        assert error is not None, 'unresolved references must be rejected'
        assert url not in str(error), 'errors must not disclose schema URLs'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('provider', ['pi', 'codex'])
def test_local_schema_references_still_validate(tmp_path, monkeypatch, provider):
    configure_provider(tmp_path, monkeypatch, provider, response_body(provider, {'answer': 'ok'}))
    schema = {
        '$id': 'https://example.invalid/schema',
        'type': 'object',
        '$defs': {'answer': {'type': 'string'}},
        'properties': {'answer': {'$ref': '#/$defs/answer'}},
        'required': ['answer'],
    }
    assert run_agent('hello', schema, provider=provider) == ({'answer': 'ok'}, {})
    configure_provider(tmp_path, monkeypatch, provider, response_body(provider, {'answer': 1}))
    with pytest.raises(ProviderError, match='invalid'):
        run_agent('hello', schema, provider=provider)


@pytest.mark.parametrize('inherit_pipes', [False, True])
def test_successful_provider_does_not_leave_background_children(tmp_path, monkeypatch, inherit_pipes):
    streams = '' if inherit_pipes else ', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL'
    configure_provider(tmp_path, monkeypatch, 'pi',
                       'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]'
                       + streams + ')\n'
                       'print(json.dumps({"pid": child.pid}))\n')
    result, _ = run_agent('hello', {}, provider='pi')
    pid = result['pid']
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.02)
        pytest.fail('provider descendant survived a completed request')
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_provider_can_write_output_before_consuming_a_large_prompt(tmp_path, monkeypatch):
    from myown_llm_integrator.process import _environment, _run
    script = tmp_path / 'duplex.py'
    script.write_text('import sys\n'
                      'sys.stderr.buffer.write(b"x" * (1024 * 1024))\n'
                      'sys.stderr.flush()\n'
                      'print(len(sys.stdin.read()))\n')
    code, stdout, stderr = _run([sys.executable, str(script)], env=_environment(),
                                cwd=str(tmp_path), timeout=3, prompt='y' * (1024 * 1024))
    assert code == 0
    assert stdout.strip() == '1048576'
    assert stderr == 'x' * (1024 * 1024)

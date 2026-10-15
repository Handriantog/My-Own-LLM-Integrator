"""Package transport tests using real subprocesses, never real accounts."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from myown_llm_integrator import ProviderError, run_codex

@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    executable = tmp_path / 'codex'
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'content': 'Hello', 'tool_calls': []}))
    executable.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys, time
args = sys.argv[1:]
state = json.loads(pathlib.Path({str(state)!r}).read_text())
if 'login' in args:
    print(state.get('login', 'Logged in using ChatGPT'), file=sys.stderr)
    sys.exit(0)
assert 'OPENAI_API_KEY' not in os.environ
assert 'CODEX_API_KEY' not in os.environ
assert 'ZEP_API_KEY' not in os.environ
assert '--ignore-user-config' in args and '--ephemeral' in args
assert '--no-daemon' in args
assert args[args.index('--sandbox') + 1] == 'read-only'
assert 'forced_login_method="chatgpt"' in args
assert 'model_provider="openai"' in args
assert 'features.shell_tool=false' in args
assert 'features.plugins=false' in args
assert 'features.hooks=false' in args
assert 'features.code_mode_host=false' in args
prompt = sys.stdin.read()
if state.get('pid_file'):
    pathlib.Path(state['pid_file']).write_text(str(os.getpid()))
if state.get('sleep'):
    time.sleep(state['sleep'])
if state.get('fail'):
    print('API_KEY=private-secret ' + state['fail'], file=sys.stderr)
    sys.exit(1)
answer = {{'content': state.get('content', ''), 'tool_calls': state.get('tool_calls', [])}}
if state.get('echo'):
    answer['content'] = prompt
output = pathlib.Path(args[args.index('-o') + 1])
output.write_text(state.get('raw', json.dumps(answer)))
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 13, 'output_tokens': 7}}}}))
''')
    executable.chmod(0o700)
    monkeypatch.setenv('CODEX_BIN', str(executable))
    monkeypatch.setenv('CODEX_LOCK_PATH', str(tmp_path / 'request.lock'))
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    monkeypatch.setenv('CODEX_API_KEY', 'must-not-inherit')
    monkeypatch.setenv('ZEP_API_KEY', 'must-not-inherit')
    monkeypatch.delenv('CODEX_MODEL', raising=False)
    return state



def test_codex_login_flags_schema_and_usage(fake_codex):
    result, usage = run_codex('hello', {'type': 'object'})
    assert result == {'content': 'Hello', 'tool_calls': []}
    assert usage == {'input_tokens': 13, 'output_tokens': 7}


def test_api_key_login_is_rejected(fake_codex):
    fake_codex.write_text(json.dumps({'login': 'Logged in using an API key'}))
    with pytest.raises(ProviderError, match='ChatGPT'):
        run_codex('hello', {})


def test_quota_errors_are_sanitized(fake_codex):
    fake_codex.write_text(json.dumps({'fail': 'rate limit exceeded'}))
    with pytest.raises(ProviderError, match='limit') as error:
        run_codex('hello', {})
    assert 'private-secret' not in str(error.value)


def test_codex_schema_violation_is_rejected(fake_codex):
    with pytest.raises(ProviderError, match='invalid'):
        run_codex('hello', {'type': 'object', 'required': ['answer']})


def test_timeout_terminates_child_and_releases_lock(fake_codex, monkeypatch):
    monkeypatch.setenv('CODEX_TIMEOUT_SECONDS', '0.2')
    fake_codex.write_text(json.dumps({'sleep': 5}))
    with pytest.raises(ProviderError, match='timed out'):
        run_codex('hello', {})
    monkeypatch.setenv('CODEX_TIMEOUT_SECONDS', '5')
    fake_codex.write_text(json.dumps({'content': 'recovered'}))
    assert run_codex('hello', {})[0]['content'] == 'recovered'


def test_calls_are_serialized_by_default(fake_codex):
    fake_codex.write_text(json.dumps({'sleep': .2, 'content': 'ok'}))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_codex('hello', {}), range(2)))
    assert [r[0]['content'] for r in results] == ['ok', 'ok']
    assert time.monotonic() - started >= .4


def test_waiting_can_be_cancelled(fake_codex):
    import threading
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ProviderError, match='cancelled'):
        run_codex('hello', {}, cancel_event=cancelled)

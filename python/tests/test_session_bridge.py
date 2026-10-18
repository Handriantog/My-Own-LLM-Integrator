"""Persistent bridge regressions; all inference uses local fake providers."""
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid

import pytest


BRIDGE = Path(__file__).resolve().parents[1] / 'session_bridge.py'


def load_bridge():
    spec = importlib.util.spec_from_file_location('session_bridge', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(**options):
    return {'id': str(uuid.uuid4()), 'operation': 'run', 'prompt': 'hello',
            'schema': {'type': 'object'}, **options}


def wire(*requests):
    return b''.join((json.dumps(value) + '\n').encode() for value in requests)


def exchange(bridge, data):
    output = io.BytesIO()
    status = bridge.serve(io.BytesIO(data), output)
    return status, [json.loads(line) for line in output.getvalue().splitlines()]


def start_bridge(env=None):
    return subprocess.Popen([sys.executable, '-I', str(BRIDGE), str(os.getpid())],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, bufsize=0)


def read_reply(process):
    assert select.select([process.stdout], [], [], 5)[0], 'bridge did not reply'
    line = process.stdout.readline()
    assert line, 'bridge exited without a reply'
    return json.loads(line)


def stop_process(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


def wait_for(predicate, message):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail(message)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def provider_environment(tmp_path, body):
    provider = tmp_path / 'provider.py'
    provider.write_text('import json, os, pathlib, subprocess, sys, time\n'
                        'prompt = sys.stdin.read().rsplit("\\n\\n", 1)[-1]\n' + body)
    return {**os.environ,
            'AGENT_COMMAND_JSON': json.dumps([sys.executable, str(provider)]),
            'AGENT_LOCK_PATH': str(tmp_path / 'requests.lock'),
            'AGENT_TIMEOUT_SECONDS': '10', 'AGENT_MODEL': 'inherited-model',
            'AGENT_ENV_PASSTHROUGH': '', 'SESSION_APP_SECRET': 'must-not-leak'}


def test_ready_is_immediate_once_and_eof_exits_without_inference():
    process = start_bridge()
    try:
        assert read_reply(process) == {'type': 'ready', 'protocol': 1}
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert stdout == b''
        assert stderr == b''
    finally:
        stop_process(process)


def test_multiple_requests_keep_ids_and_measure_each_engine_call(monkeypatch):
    bridge = load_bridge()
    calls = []

    def run(prompt, schema, **options):
        calls.append((prompt, schema, options))
        time.sleep(.005)
        return {'answer': prompt}, {'input_tokens': len(prompt)}

    monkeypatch.setattr(bridge, 'run_agent', run)
    first, second = request(prompt='one'), request(prompt='two', provider='pi', model=None)
    status, replies = exchange(bridge, wire(first, second))
    assert status == 0
    assert replies[0] == {'type': 'ready', 'protocol': 1}
    assert len(replies) == 3
    for expected, reply in zip((first, second), replies[1:]):
        assert reply['id'] == expected['id']
        assert reply['ok'] is True
        assert reply['value'] == {'result': {'answer': expected['prompt']},
                                  'usage': {'input_tokens': 3}}
        assert reply['inferenceMs'] >= 4
    assert 'model' not in calls[0][2]
    assert calls[1][2]['model'] is None


@pytest.mark.parametrize('bad', [
    {'operation': 'providers'}, {'prompt': 12}, {'schema': []}, {'schema': None},
    {'provider': None}, {'provider': 12}, {'provider': ' '}, {'model': ''},
    {'model': ' '}, {'model': 12}, {'model': False}, {'unknown': 'field'},
])
def test_invalid_fields_are_rejected_before_engine_and_next_request_works(monkeypatch, bad):
    bridge = load_bridge()
    calls = []

    def run(prompt, schema, **options):
        calls.append(prompt)
        return {'answer': prompt}, {}

    monkeypatch.setattr(bridge, 'run_agent', run)
    invalid, valid = request(**bad), request(prompt='next')
    status, replies = exchange(bridge, wire(invalid, valid))
    assert status == 0
    assert replies[1]['id'] == invalid['id']
    assert replies[1]['ok'] is False
    assert replies[2]['id'] == valid['id']
    assert replies[2]['ok'] is True
    assert calls == ['next']


@pytest.mark.parametrize('identifier', [None, 42, '', 'not-a-uuid', 'a' * 32, ['uuid']])
def test_invalid_ids_are_not_echoed_and_do_not_poison_next_request(monkeypatch, identifier):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, 'run_agent', lambda *args, **kwargs: ({}, {}))
    status, replies = exchange(bridge, wire(request(id=identifier), request()))
    assert status == 0
    assert replies[1]['id'] is None
    assert replies[1]['ok'] is False
    assert replies[2]['ok'] is True


@pytest.mark.parametrize('field', ['id', 'operation', 'prompt', 'schema'])
def test_required_fields_cannot_be_omitted(monkeypatch, field):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, 'run_agent', lambda *args, **kwargs: pytest.fail('invalid request ran'))
    invalid = request()
    del invalid[field]
    status, replies = exchange(bridge, wire(invalid))
    assert status == 0
    assert replies[1]['ok'] is False


def test_provider_errors_are_safe_other_errors_are_generic_and_session_recovers(monkeypatch):
    bridge = load_bridge()

    def run(prompt, schema, **options):
        if prompt == 'provider':
            raise bridge.ProviderError('Provider request failed.')
        if prompt == 'unexpected':
            raise RuntimeError('private credential and prompt')
        return {}, {}

    monkeypatch.setattr(bridge, 'run_agent', run)
    status, replies = exchange(bridge, wire(request(prompt='provider'),
                                            request(prompt='unexpected'), request()))
    assert status == 0
    assert replies[1]['error'] == 'Provider request failed.'
    assert replies[2]['ok'] is False
    assert 'private' not in replies[2]['error']
    assert replies[3]['ok'] is True


@pytest.mark.parametrize('data', [b'{bad}\n', b'[]\n', b'null\n', b'\xff\n',
                                 b'{"id": 1, "id": 2}\n', b'{"schema":NaN}\n',
                                 b'{"schema":1e999}\n'])
def test_malformed_json_exits_with_a_safe_protocol_failure(monkeypatch, data):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, 'run_agent', lambda *args, **kwargs: pytest.fail('malformed request ran'))
    status, replies = exchange(bridge, data + wire(request()))
    assert status != 0
    assert len(replies) == 2
    assert replies[1]['id'] is None
    assert replies[1]['ok'] is False


def test_input_limit_is_bytes_and_read_is_bounded_before_parsing(monkeypatch):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, 'run_agent', lambda *args, **kwargs: pytest.fail('oversized request ran'))

    class BoundedInput(io.BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= 8 * 1024 * 1024 + 1
            return super().readline(size)

    output = io.BytesIO()
    data = wire(request(prompt='é' * (8 * 1024 * 1024 // 2)))
    status = bridge.serve(BoundedInput(data), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert status != 0
    assert replies[-1]['ok'] is False
    assert len(output.getvalue()) < 1024


@pytest.mark.parametrize('value', [{'too_big': '\x00' * (6 * 1024 * 1024)},
                                 {'not_finite': float('nan')}, {'not_finite': float('inf')}])
def test_response_is_bounded_and_serialization_failure_never_partially_succeeds(monkeypatch, value):
    bridge = load_bridge()
    results = iter([(value, {}), ({'answer': 'next'}, {})])
    monkeypatch.setattr(bridge, 'run_agent', lambda *args, **kwargs: next(results))
    status, replies = exchange(bridge, wire(request(), request()))
    assert status == 0
    assert len(replies) == 3
    assert replies[1]['ok'] is False
    assert replies[2]['value']['result'] == {'answer': 'next'}
    assert len(json.dumps(replies).encode()) < 1024


def test_real_engine_preserves_model_defaults_environment_validation_and_cleanup(tmp_path):
    env = provider_environment(tmp_path,
        'if prompt == "fail":\n'
        '    print("private provider diagnostic", file=sys.stderr)\n'
        '    sys.exit(1)\n'
        'print(json.dumps({"model": os.environ["MYOWN_LLM_MODEL"], '
        '"secret": os.environ.get("SESSION_APP_SECRET"), "cwd": os.getcwd(), '
        '"pid": os.getpid(), "prompt": prompt}))\n')
    requests = [request(provider='pi', schema={'type': 'invalid-schema'}),
                request(provider='pi', prompt='fail'), request(provider='pi'),
                request(provider='pi', model=None), request(provider='pi', model='explicit')]
    process = start_bridge(env)
    try:
        stdout, stderr = process.communicate(wire(*requests), timeout=10)
        assert process.returncode == 0
        assert stderr == b''
        replies = [json.loads(line) for line in stdout.splitlines()]
        assert replies[0] == {'type': 'ready', 'protocol': 1}
        assert replies[1]['ok'] is False
        assert replies[2]['ok'] is False
        assert 'private provider diagnostic' not in replies[2]['error']
        results = [reply['value']['result'] for reply in replies[3:]]
        assert [result['model'] for result in results] == ['inherited-model', '', 'explicit']
        assert len({result['pid'] for result in results}) == 3
        for result in results:
            assert result['secret'] is None
            assert not Path(result['cwd']).exists()
    finally:
        stop_process(process)


def busy_environment(tmp_path):
    marker = tmp_path / 'active.json'
    env = provider_environment(tmp_path,
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])\n'
        f'pathlib.Path({str(marker)!r}).write_text(json.dumps('
        '{"pid": os.getpid(), "child": child.pid, "cwd": os.getcwd()}))\n'
        'time.sleep(30)\n')
    return marker, env


def assert_provider_cleaned(marker):
    state = json.loads(marker.read_text())
    wait_for(lambda: not alive(state['pid']) and not alive(state['child']),
             'provider process or descendant survived session termination')
    assert not Path(state['cwd']).exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX session lifecycle')
@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize('busy', [False, True])
def test_signals_exit_permanently_and_cleanup_active_provider(tmp_path, signum, busy):
    marker, env = busy_environment(tmp_path)
    process = start_bridge(env)
    try:
        assert read_reply(process)['type'] == 'ready'
        if busy:
            process.stdin.write(wire(request(provider='pi')))
            process.stdin.flush()
            wait_for(marker.exists, 'provider did not start')
        process.send_signal(signum)
        process.wait(timeout=5)
        assert process.returncode != 0
        stdout, stderr = process.communicate(timeout=5)
        assert stdout == b''
        assert stderr == b''
        if busy:
            assert_provider_cleaned(marker)
    finally:
        stop_process(process)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX parent watchdog')
def test_parent_death_exits_session_and_cleans_active_provider(tmp_path):
    marker, env = busy_environment(tmp_path)
    bridge_pid_path = tmp_path / 'bridge.pid'
    parent_source = (
        'import os, pathlib, subprocess, sys, time\n'
        f'bridge = subprocess.Popen([sys.executable, "-I", {str(BRIDGE)!r}, str(os.getpid())], '
        'stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
        f'pathlib.Path({str(bridge_pid_path)!r}).write_text(str(bridge.pid))\n'
        f'bridge.stdin.write({wire(request(provider="pi"))!r})\n'
        'bridge.stdin.flush()\n'
        'time.sleep(30)\n'
    )
    parent = subprocess.Popen([sys.executable, '-I', '-c', parent_source], env=env)
    try:
        wait_for(marker.exists, 'provider did not start under parent')
        parent.kill()
        parent.wait(timeout=5)
        assert_provider_cleaned(marker)
        bridge_pid = int(bridge_pid_path.read_text())
        wait_for(lambda: not alive(bridge_pid), 'bridge survived its parent')
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if bridge_pid_path.exists():
            pid = int(bridge_pid_path.read_text())
            if alive(pid):
                os.kill(pid, signal.SIGKILL)

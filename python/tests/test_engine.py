import json
import os
import sys
from pathlib import Path

import pytest


def test_engine_is_independent_of_researcher_and_model_is_explicit(tmp_path, monkeypatch):
    from myown_llm_integrator import run_agent
    executable = tmp_path / 'wrapper.py'
    executable.write_text('import json, os, sys\n'
                          'assert "Output JSON Schema" in sys.stdin.read()\n'
                          'print(json.dumps({"model": os.environ["MYOWN_LLM_MODEL"]}))\n')
    monkeypatch.setenv('AGENT_COMMAND_JSON', json.dumps([sys.executable, str(executable)]))
    monkeypatch.setenv('AGENT_LOCK_PATH', str(tmp_path / 'requests.lock'))
    monkeypatch.setenv('AGENT_MODEL', 'environment-model')
    result, usage = run_agent('hello', {'type': 'object'}, provider='pi', model='explicit')
    assert result == {'model': 'explicit'}
    assert usage == {}
    assert 'research_runtime' not in sys.modules
    result, _ = run_agent('hello', {}, provider='pi', model=None)
    assert result == {'model': ''}
    assert os.environ['AGENT_MODEL'] == 'environment-model'


def test_default_lock_is_user_state_not_the_installation(tmp_path, monkeypatch):
    from myown_llm_integrator.process import default_lock_path
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    assert default_lock_path('CODEX') == tmp_path / 'myown-llm-integrator' / 'codex.lock'

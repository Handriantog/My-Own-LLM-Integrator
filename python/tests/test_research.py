"""Observed provider metadata is not model-authored citation evidence."""
import json
import time
import pytest
from myown_llm_integrator.research import search_record, run_research
from myown_llm_integrator.errors import ProviderError
from myown_llm_integrator.process import _request_slot

def test_search_records_reject_non_array_results():
    with pytest.raises(ProviderError):
        search_record({'query':'query','action':{'type':'search'},'results':{'url':'https://example.com'}})

def test_capture_candidates_reject_credentials_and_unbounded_result_sets():
    result=search_record({'query':'query','action':{'type':'openPage','url':'https://user:secret@example.com'},'results':[{'url':'file:///etc/passwd'},{'url':'https://example.com/a'}]})
    assert result=={'query':'query','urls':['https://example.com/a']}
    with pytest.raises(ProviderError):
        search_record({'query':'query','action':{'type':'search'},'results':[{}]*101})

def test_python_research_rejects_external_schema_without_a_provider():
    with pytest.raises(ProviderError,match='External schema'):
        run_research({'prompt':'question','schema':{'$ref':'https://example.com/schema'},'webSearch':False})

def test_expired_request_does_not_acquire_shared_slot(tmp_path,monkeypatch):
    monkeypatch.setenv('CODEX_LOCK_PATH',str(tmp_path/'request.lock'))
    with pytest.raises(ProviderError,match='timed out'):
        with _request_slot(deadline=time.monotonic()-1):
            pytest.fail('deadline admitted provider work')

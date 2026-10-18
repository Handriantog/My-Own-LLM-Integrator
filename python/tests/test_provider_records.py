import json
import pytest


def parser(names=()):
    from myown_llm_integrator.provider_session import Records
    accepted = []
    return Records(list(names), accepted.append), accepted


def test_complete_records_commit_before_done_and_preserve_unicode():
    records, accepted = parser()
    records.feed('{"type":"text","text":"你好 🌍. "}\n{"type":"te')
    assert accepted == [{'sequence': 0, 'text': '你好 🌍. '}]
    records.feed('xt","text":"Next. "}\n{"type":"done"}\n')
    assert records.finish() == {'text': '你好 🌍. Next. ', 'toolCalls': []}
    assert accepted[1] == {'sequence': 1, 'text': 'Next. '}


@pytest.mark.parametrize('tail', [
    '{"type":"tools","toolCalls":[]}\n',
    '{"type":"text","text":"bad","extra":1}\n',
    '{"type":"text","text":"bad","text":"duplicate"}\n',
    'not json\n', '{"type":"done","extra":true}\n',
])
def test_invalid_tail_never_retracts_accepted_text(tail):
    records, accepted = parser()
    records.feed('{"type":"text","text":"Saved. "}\n')
    with pytest.raises(Exception):
        records.feed(tail)
        records.finish()
    assert accepted == [{'sequence': 0, 'text': 'Saved. '}]


def test_tools_only_buffered_and_arguments_validate():
    records, accepted = parser(['lookup'])
    records.feed('{"type":"tools","toolCalls":[{"name":"lookup","argumentsJson":"{\\"q\\":\\"hello\\"}"}]}\n')
    assert accepted == []
    with pytest.raises(Exception): records.finish()
    records.feed('{"type":"done"}\n')
    assert records.finish() == {'text': '', 'toolCalls': [{'name': 'lookup', 'argumentsJson': '{"q":"hello"}'}]}


@pytest.mark.parametrize('value', [
    {'type': 'tools', 'toolCalls': [{'name': 'forbidden', 'argumentsJson': '{}'}]},
    {'type': 'tools', 'toolCalls': [{'name': 'lookup', 'argumentsJson': '[]'}]},
    {'type': 'tools', 'toolCalls': [{'name': 'lookup', 'argumentsJson': '{"x":NaN}'}]},
    {'type': 'tools', 'toolCalls': [{'name': 'lookup', 'argumentsJson': '{"x":1,"x":2}'}]},
    {'type': 'tools', 'toolCalls': []},
    {'type': 'text', 'text': ''}, {'type': 'text', 'text': ' '},
    {'type': 'text', 'text': 'x' * 401},
    {'type': 'done'},
])
def test_reject_invalid_records(value):
    records, accepted = parser(['lookup'])
    with pytest.raises(Exception): records.feed(json.dumps(value) + '\n')
    assert accepted == []


def test_record_count_total_and_unterminated_bounds():
    records, accepted = parser()
    with pytest.raises(Exception): records.feed(' ' * 65537)
    records, accepted = parser()
    with pytest.raises(Exception):
        for _ in range(257): records.feed('{"type":"text","text":"x"}\n')
    assert len(accepted) == 256
    records, accepted = parser()
    with pytest.raises(Exception):
        for _ in range(257): records.feed(json.dumps({'type': 'text', 'text': 'x' * 400}) + '\n')
    assert len(accepted) == 256


def test_done_is_terminal_and_partial_record_not_accepted():
    records, accepted = parser()
    records.feed('{"type":"text","text":"partial"}')
    assert accepted == []
    with pytest.raises(Exception): records.finish()
    records.feed('\n{"type":"done"}\n')
    with pytest.raises(Exception): records.feed('{"type":"text","text":"late"}\n')
    assert len(accepted) == 1


def test_utf16_text_limit_matches_node_and_surrogate_is_rejected():
    records, accepted = parser()
    with pytest.raises(Exception): records.feed(json.dumps({'type':'text','text':'🌍' * 201}) + '\n')
    with pytest.raises(Exception): records.feed('{"type":"text","text":"\\ud800"}\n')
    assert accepted == []


def test_config_tools_must_be_verified_from_highest_layer_when_v2_omits_fields():
    from myown_llm_integrator.provider_session import effective_config, verify_config, SETTINGS
    config = {}
    for key,value in SETTINGS.items():
        target = config
        parts=key.split('.')
        for part in parts[:-1]: target=target.setdefault(part,{})
        target[parts[-1]]=value
    tools = config.pop('tools')
    result={'config':config,'layers':[{'name':{'type':'sessionFlags'},'config':{'tools':tools}}]}
    assert verify_config(effective_config(result)) == {}
    result['layers'].insert(0,{'name':{'type':'mdm'},'config':{'tools':{'update_plan':{'enabled':True}}}})
    with pytest.raises(Exception): verify_config(effective_config(result))


@pytest.mark.parametrize('requirements', [
    {'featureRequirements':{'shell_tool':True}},
    {'allowedLoginMethods':['api']},
    {'allowedSandboxModes':['danger-full-access']},
    {'allowedApprovalPolicies':['on-request']},
    {'chatgptBaseUrl':'https://example.invalid'},
    {'additionalDeveloperInstructions':'Unreviewed managed instructions'},
])
def test_managed_requirements_cannot_reenable_capabilities_or_redirect_auth(requirements):
    from myown_llm_integrator.provider_session import verify_requirements
    with pytest.raises(Exception): verify_requirements(requirements)

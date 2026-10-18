"""Private Codex app-server transport and strict, independently committed records."""
import json
import copy
import math
import re
from .errors import ProviderError


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Nonfinite number')
    return number


def _constant(value):
    raise ValueError('Nonfinite number')


def decode(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_float=_number, parse_constant=_constant)


def fail(message='Invalid provider decision record.'):
    raise ProviderError(message)


class Records:
    MAX_RECORD = 65536
    MAX_TEXT = 400
    MAX_TOTAL = 102400
    MAX_RECORDS = 256

    def __init__(self, tool_names, accept):
        self.names = set(tool_names)
        self.accept = accept
        self.pending = ''
        self.text = []
        self.tools = []
        self.mode = None
        self.done = False
        self.total = 0

    def feed(self, delta):
        if not isinstance(delta, str):
            fail()
        # Bound each partial line, even when one delta contains many records.
        for part in delta.splitlines(keepends=True):
            self.pending += part
            if len(self.pending.encode('utf-8')) > self.MAX_RECORD:
                fail('Provider decision record exceeded the size limit.')
            if self.pending.endswith('\n'):
                line, self.pending = self.pending, ''
                self.record(line)

    def record(self, line):
        if self.done:
            fail('Provider emitted data after done.')
        try:
            value = decode(line)
        except (ValueError, RecursionError):
            fail()
        if not isinstance(value, dict):
            fail()
        kind = value.get('type')
        if kind == 'text' and set(value) == {'type', 'text'}:
            text = value['text']
            if self.mode not in (None, 'text') or not isinstance(text, str) or not text.strip():
                fail()
            try:
                size = len(text.encode('utf-16-le')) // 2
            except UnicodeError:
                fail()
            if size > self.MAX_TEXT or self.total + size > self.MAX_TOTAL or len(self.text) >= self.MAX_RECORDS:
                fail('Provider text exceeded the size limit.')
            self.mode = 'text'
            sequence = len(self.text)
            self.accept({'sequence': sequence, 'text': text})
            self.text.append(text)
            self.total += size
        elif kind == 'tools' and set(value) == {'type', 'toolCalls'}:
            calls = value['toolCalls']
            if self.mode is not None or not isinstance(calls, list) or not 1 <= len(calls) <= 16:
                fail()
            for call in calls:
                if not isinstance(call, dict) or set(call) != {'name', 'argumentsJson'}:
                    fail()
                if not isinstance(call['name'], str) or call['name'] not in self.names:
                    fail()
                raw = call['argumentsJson']
                if not isinstance(raw, str) or len(raw.encode('utf-8')) > 32768:
                    fail()
                try:
                    arguments = decode(raw)
                except (ValueError, RecursionError):
                    fail()
                if not isinstance(arguments, dict):
                    fail()
            self.mode = 'tools'
            self.tools = calls
        elif kind == 'done' and set(value) == {'type'} and self.mode is not None:
            self.done = True
        else:
            fail()

    def finish(self):
        if not self.done or self.pending:
            fail('Provider decision did not finish with a complete done record.')
        return {'text': ''.join(self.text), 'toolCalls': self.tools}

import os
import selectors
import shutil
import subprocess
import tempfile
import time
from .process import _environment, _positive_seconds, _request_slot, _run

# The installed 0.157.1 tool planner also requires environments=[] to remove
# environment-bound tools (including apply_patch); shell flags alone do not.
DISABLED_FEATURES = (
    'shell_tool', 'unified_exec', 'code_mode', 'code_mode_host', 'apps', 'plugins',
    'hooks', 'multi_agent', 'multi_agent_v2', 'browser_use', 'computer_use',
    'image_generation', 'view_image', 'shell_snapshot', 'js_repl', 'rmcp_client',
    'remote_control', 'remote_plugin', 'tool_suggest', 'auth_elicitation',
    'memories', 'api_key_model_discovery',
)
SETTINGS = {
    'model_provider': 'openai', 'forced_login_method': 'chatgpt',
    'approval_policy': 'never', 'sandbox_mode': 'read-only',
    'project_doc_max_bytes': 0, 'web_search': 'disabled', 'notify': [],
    'allow_login_shell': False, 'experimental_use_unified_exec_tool': False,
    'include_environment_context': False, 'include_apps_instructions': False,
    'include_collaboration_mode_instructions': False,
    'skills.include_instructions': False, 'skills.bundled.enabled': False,
    'tools.update_plan.enabled': False, 'tools.experimental_request_user_input.enabled': False,
    'history.persistence': 'none', 'analytics.enabled': False,
    'check_for_update_on_startup': False, 'thread_unload_delay_secs': 0,
    'features.skip_host_skill_discovery': True,
    'suppress_unstable_features_warning': True,
    **{'features.' + name: False for name in DISABLED_FEATURES},
}
INSTRUCTIONS = (
    'You are the inference component of a local application. Complete the supplied '
    'conversation as its assistant, obeying all its system instructions. The current '
    'authoritative context snapshot supersedes previous turn context. Do not access '
    'files, execute tools, browse, or act on the host computer. Proposed application '
    'actions are data only. Return only newline-delimited JSON records, no markdown. '
    'Choose exactly one mode: text records {"type":"text","text":"One complete sentence. "} '
    'or one {"type":"tools","toolCalls":[{"name":"allowed_name","argumentsJson":"{}"}]}. '
    'Never mix text and tools. Each text record must be a complete speakable sentence '
    'or phrase, at most 400 characters. Maximum 256 text records. Each record is '
    'irrevocable once emitted. Tool names must come from the supplied allowed list and '
    'argumentsJson must encode a JSON object. Finish with {"type":"done"} on its own '
    'line, including a final newline. Do not put instructions, commentary or reasoning '
    'outside these records. The transport format overrides any conflicting envelope '
    'format in the supplied snapshot, preserving its full substantive instructions.'
)


def nested(config, path):
    value = config
    for key in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def effective_config(response):
    config = copy.deepcopy(response.get('config'))
    if not isinstance(config, dict):
        fail('Cannot verify provider configuration.')
    # ConfigV2 omits these two recognized tool fields. Resolve them from the
    # highest-priority active raw layer (the server returns descending priority).
    for path in ('tools.update_plan.enabled', 'tools.experimental_request_user_input.enabled', 'thread_unload_delay_secs'):
        if nested(config, path) is not None:
            continue
        layers = response.get('layers')
        if not isinstance(layers, list):
            fail('Cannot verify provider utility tool or lifecycle restrictions.')
        for layer in layers:
            if layer.get('disabledReason'):
                continue
            value = nested(layer.get('config'), path)
            if value is not None:
                target = config
                parts = path.split('.')
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
                break
    return config


def verify_config(config, *, mcp=True):
    if not isinstance(config, dict):
        fail('Cannot verify provider restrictions.')
    for key, expected in SETTINGS.items():
        if nested(config, key) != expected:
            fail('Cannot establish provider restrictions; effective configuration differs.')
    for key, allowed in [('openai_base_url', (None, 'https://api.openai.com/v1')),
                         ('chatgpt_base_url', (None, 'https://chatgpt.com/backend-api', 'https://chatgpt.com/backend-api/'))]:
        if config.get(key) not in allowed:
            fail('Provider base URL overrides are not allowed in subscription sessions.')
    servers = config.get('mcp_servers', {})
    if not isinstance(servers, dict) or len(servers) > 256:
        fail('Cannot verify provider MCP restrictions.')
    if mcp and any(not isinstance(server, dict) or server.get('enabled') is not False for server in servers.values()):
        fail('Cannot disable inherited provider MCP servers.')
    return servers


def verify_requirements(requirements):
    if requirements is None:
        return
    if not isinstance(requirements, dict):
        fail('Cannot verify managed provider requirements.')
    for key, wanted in [('allowedLoginMethods', 'chatgpt'), ('allowedSandboxModes', 'read-only'),
                        ('allowedApprovalPolicies', 'never'), ('allowedWebSearchModes', 'disabled')]:
        allowed = requirements.get(key)
        if allowed is not None and (not isinstance(allowed, list) or wanted not in allowed):
            fail('Managed provider requirements conflict with restricted subscription sessions.')
    features = requirements.get('featureRequirements') or {}
    if (not isinstance(features, dict) or any(features.get(key) is True for key in DISABLED_FEATURES)
        or features.get('skip_host_skill_discovery') is False
        or requirements.get('modelProvider') not in (None, 'openai')
        or requirements.get('modelProviders')
        or requirements.get('chatgptBaseUrl') not in (None, 'https://chatgpt.com/backend-api', 'https://chatgpt.com/backend-api/')
        or requirements.get('additionalDeveloperInstructions')
        or requirements.get('modelCatalogJson')
        or requirements.get('defaultPermissions')):
        fail('Managed provider requirements conflict with restricted subscription sessions.')
    hooks = requirements.get('hooks') or {}
    if not isinstance(hooks, dict) or any(hooks.values()):
        fail('Managed hooks are not permitted in restricted provider sessions.')


class AppServer:
    """One JSONL process; every read is bounded, including ignored diagnostics."""
    LIMIT = 32 * 1024 * 1024
    FRAME_LIMIT = 2 * 1024 * 1024

    def __init__(self, executable, directory, settings):
        args = [executable, '--no-daemon', 'app-server', '--listen', 'stdio://']
        for key, value in settings.items():
            args.extend(['-c', key + '=' + json.dumps(value, ensure_ascii=False)])
        self.process = subprocess.Popen(args, cwd=directory, env=_environment(),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0)
        self.selector = selectors.DefaultSelector()
        for stream in (self.process.stdout, self.process.stderr):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ)
        self.pending = bytearray()
        self.size = 0
        self.identifier = 0
        self.events = []
        self.deadline = time.monotonic() + 30

    def write(self, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
        # The Node deadline can interrupt blocked writes as well as reads.
        offset = 0
        while offset < len(raw):
            written = self.process.stdin.write(raw[offset:])
            if not written:
                fail('Provider input pipe closed.')
            offset += written
        self.process.stdin.flush()

    def read(self):
        while True:
            newline = self.pending.find(b'\n')
            if newline >= 0:
                raw = bytes(self.pending[:newline])
                del self.pending[:newline + 1]
                if len(raw) > self.FRAME_LIMIT:
                    fail('Provider protocol frame exceeded the size limit.')
                try:
                    value = decode(raw.decode('utf-8'))
                except (ValueError, UnicodeError, RecursionError):
                    fail('Invalid provider protocol response.')
                if not isinstance(value, dict):
                    fail('Invalid provider protocol response.')
                if value.get('method') in ('error', 'model/rerouted'):
                    fail('Provider failed or rerouted the model; no retry was used.')
                if 'method' in value and 'id' in value:
                    fail('Provider requested an unsupported native action or approval.')
                return value
            if len(self.pending) > self.FRAME_LIMIT:
                fail('Provider protocol frame exceeded the size limit.')
            if self.process.poll() is not None:
                fail('Provider process exited before completion.')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                fail('Provider request timed out.')
            for key, _ in self.selector.select(min(.1, remaining)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                self.size += len(chunk)
                if self.size > self.LIMIT:
                    fail('Provider output exceeded the size limit.')
                if key.fileobj is self.process.stdout:
                    self.pending.extend(chunk)

    def rpc(self, method, params):
        self.identifier += 1
        identifier = self.identifier
        self.write({'id': identifier, 'method': method, 'params': params})
        while True:
            value = self.read()
            if type(value.get('id')) is int and value.get('id') == identifier:
                if 'error' in value or not isinstance(value.get('result'), dict):
                    fail('Provider request failed; no retry or API fallback was used.')
                return value['result']
            if 'id' in value:
                fail('Unexpected provider response identity.')
            self.events.append(value)
            if len(self.events) > 4096:
                fail('Too many provider protocol events.')

    def event(self):
        return self.events.pop(0) if self.events else self.read()

    def close(self):
        self.process.kill() if self.process.poll() is None else None
        self.process.wait()
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class ProviderSession:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='myown-provider-')
        self.cwd = os.path.realpath(self.directory.name)
        self.server = None
        self.thread = None
        self.default_model = None
        self.model_defaults = {}
        self.effective_config = None
        try:
            executable = shutil.which(os.environ.get('CODEX_BIN', 'codex'))
            if not executable:
                fail('Codex CLI not found. Install it or set CODEX_BIN.')
            with _request_slot():
                code, stdout, _ = _run([executable, '--version'], env=_environment(), cwd=self.cwd, timeout=15)
                if code or stdout.strip() != 'codex-cli 0.157.1':
                    fail('Unsupported Codex version for restricted provider sessions; expected 0.157.1.')
                code, stdout, stderr = _run([executable, '--no-daemon', 'login', 'status'],
                    env=_environment(), cwd=self.cwd, timeout=15)
                if code or 'logged in using chatgpt' not in (stdout + stderr).lower():
                    fail('Codex requires a ChatGPT login; no API fallback was used.')
                self.server = self.start(executable, SETTINGS)
                config = effective_config(self.server.rpc('config/read', {'includeLayers': True, 'cwd': self.cwd}))
                servers = verify_config(config, mcp=False)
                verify_requirements(self.server.rpc('configRequirements/read', {})['requirements'])
                # CLI -c empty tables merge; disable every inherited entry by name.
                if servers:
                    overrides = dict(SETTINGS)
                    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name) for name in servers):
                        fail('Cannot safely override inherited MCP server names.')
                    overrides.update({'mcp_servers.' + name + '.enabled': False for name in servers})
                    self.server.close()
                    self.server = self.start(executable, overrides)
                    config = effective_config(self.server.rpc('config/read', {'includeLayers': True, 'cwd': self.cwd}))
                verify_config(config)
                verify_requirements(self.server.rpc('configRequirements/read', {})['requirements'])
                self.effective_config = config
                self._load_model_defaults()
                account = self.server.rpc('account/read', {'refreshToken': False}).get('account')
                if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                    fail('Codex requires a ChatGPT account; no API fallback was used.')
                # Discard startup notifications without leaking configuration.
                self.server.events.clear()
        except BaseException:
            self.close()
            raise

    def start(self, executable, settings):
        server = AppServer(executable, self.cwd, settings)
        try:
            server.rpc('initialize', {'clientInfo': {'name': 'myown_llm_integrator', 'version': '0.1.0'},
                                     'capabilities': {'experimentalApi': True}})
            server.write({'method': 'initialized', 'params': {}})
            return server
        except BaseException:
            server.close()
            raise

    def _load_model_defaults(self):
        # exec --ignore-user-config uses catalog defaults. Do not inherit a coding
        # session's model/effort merely because app-server must read user config.
        cursor = None
        defaults = []
        seen = set()
        for _ in range(16):
            page = self.server.rpc('model/list', {'includeHidden': True, 'limit': 100, 'cursor': cursor})
            if not isinstance(page.get('data'), list):
                fail('Cannot read provider model defaults.')
            for item in page['data']:
                model = item.get('model') if isinstance(item, dict) else None
                effort = item.get('defaultReasoningEffort') if isinstance(item, dict) else None
                if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
                    fail('Invalid provider model defaults.')
                self.model_defaults[model] = effort
                if item.get('isDefault') is True:
                    defaults.append(model)
            cursor = page.get('nextCursor')
            if cursor is None:
                break
            if not isinstance(cursor, str) or cursor in seen:
                fail('Invalid provider model pagination.')
            seen.add(cursor)
        else:
            fail('Provider model catalog exceeded the limit.')
        if len(defaults) != 1:
            fail('Cannot select the provider default model.')
        self.default_model = defaults[0]

    def _start_thread(self, phases):
        server = self.server
        # Recheck immediately before capabilities are materialized.
        checking = time.monotonic()
        config = effective_config(server.rpc('config/read', {'includeLayers': True, 'cwd': self.cwd}))
        verify_config(config)
        phases['threadConfigReadMs'] = (time.monotonic() - checking) * 1000
        creating = time.monotonic()
        response = server.rpc('thread/start', {
            'modelProvider': 'openai', 'allowProviderModelFallback': False,
            'model': self.default_model, 'serviceTier': 'default',
            'config': {'model_reasoning_effort': self.model_defaults[self.default_model]},
            'cwd': self.cwd, 'runtimeWorkspaceRoots': [],
            'approvalPolicy': 'never', 'sandbox': 'read-only', 'ephemeral': True,
            'baseInstructions': INSTRUCTIONS, 'developerInstructions': '',
            'environments': [], 'dynamicTools': [], 'selectedCapabilityRoots': [],
            'experimentalRawEvents': False,
        })
        if (response.get('modelProvider') != 'openai' or response.get('approvalPolicy') != 'never'
            or response.get('sandbox') != {'type': 'readOnly', 'networkAccess': False}
            or response.get('cwd') != self.cwd or response.get('instructionSources') != []
            or response.get('model') != self.default_model
            or response.get('reasoningEffort') != self.model_defaults[self.default_model]):
            fail('Cannot verify provider thread restrictions.')
        self.thread = response['thread']['id']
        phases['threadCreateMs'] = (time.monotonic() - creating) * 1000

    def _release_thread(self):
        thread = self.thread
        response = self.server.rpc('thread/unsubscribe', {'threadId': thread})
        if response.get('status') != 'unsubscribed':
            fail('Cannot release provider decision context.')
        # With zero unload delay, wait for the exact thread to close before reuse.
        self.server.deadline = min(self.server.deadline, time.monotonic() + 5)
        while True:
            event = self.server.event()
            method, params = event.get('method'), event.get('params', {})
            if method == 'thread/closed' and params.get('threadId') == thread:
                self.thread = None
                return
            if method == 'deprecationNotice' or (method == 'warning' and params.get('threadId') in (None, thread)):
                continue
            if method == 'thread/started' and params.get('thread', {}).get('id') == thread:
                continue
            if method not in ('thread/status/changed', 'thread/tokenUsage/updated') or params.get('threadId') != thread:
                fail('Unexpected event while releasing provider context.')

    def run(self, request, accept):
        started = time.monotonic()
        # Per-request metadata only. Never retain raw text or provider payloads.
        phases = {'lockWaitMs': 0.0, 'preparationMs': 0.0, 'turnStartAckMs': 0.0,
                  'accountReadMs': 0.0, 'requirementsReadMs': 0.0, 'configReadMs': 0.0,
                  'threadConfigReadMs': 0.0, 'threadCreateMs': 0.0,
                  'firstDeltaMs': None, 'firstValidatedTextMs': None,
                  'turnCompletedMs': 0.0, 'textCallbackMs': 0.0, 'cleanupMs': 0.0, 'totalMs': 0.0}
        self.phases = phases
        with _request_slot() as waited:
            phases['lockWaitMs'] = waited * 1000
            preparing = time.monotonic()
            server = self.server
            server.size = 0
            server.deadline = time.monotonic() + _positive_seconds('CODEX_TIMEOUT_SECONDS', '180')
            checking = time.monotonic()
            account = server.rpc('account/read', {'refreshToken': False}).get('account')
            if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                fail('Codex requires a ChatGPT account; no API fallback was used.')
            phases['accountReadMs'] = (time.monotonic() - checking) * 1000
            checking = time.monotonic()
            verify_requirements(server.rpc('configRequirements/read', {})['requirements'])
            phases['requirementsReadMs'] = (time.monotonic() - checking) * 1000
            checking = time.monotonic()
            verify_config(effective_config(server.rpc('config/read', {'includeLayers': True, 'cwd': self.cwd})))
            phases['configReadMs'] = (time.monotonic() - checking) * 1000
            if self.thread is None:
                self._start_thread(phases)
            model = request.get('model', os.environ.get('CODEX_MODEL') or self.default_model) or self.default_model
            if model not in self.model_defaults:
                fail('Selected model is absent from the provider catalog; no model fallback was used.')
            prompt = ('Allowed application tool names (data only): ' + json.dumps(request['toolNames']) +
                      '\nCurrent authoritative context snapshot:\n' + request['prompt'])
            dispatched = time.monotonic()
            phases['preparationMs'] = (dispatched - preparing) * 1000
            turn = server.rpc('turn/start', {
                'threadId': self.thread, 'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
                'model': model, 'effort': self.model_defaults[model], 'serviceTierForTurn': 'default',
                'environments': [], 'runtimeWorkspaceRoots': [],
                'approvalPolicy': 'never', 'sandboxPolicy': {'type': 'readOnly', 'networkAccess': False},
            })['turn']['id']
            phases['turnStartAckMs'] = (time.monotonic() - dispatched) * 1000
            def timed_accept(span):
                accepting = time.monotonic()
                if phases['firstValidatedTextMs'] is None:
                    phases['firstValidatedTextMs'] = (accepting - dispatched) * 1000
                try:
                    accept(span)
                finally:
                    phases['textCallbackMs'] += (time.monotonic() - accepting) * 1000
            records = Records(request['toolNames'], timed_accept)
            message = None
            completed = False
            buffered = False
            raw = ''
            usage = {}
            ignored_items = set()
            while True:
                if time.monotonic() >= server.deadline:
                    fail('Provider request timed out.')
                event = server.event()
                method = event.get('method', '')
                params = event.get('params', {})
                if not isinstance(params, dict):
                    fail('Invalid provider event.')
                if method in ('error', 'model/rerouted'):
                    fail('Provider turn failed; no retry or model fallback was used.')
                if method.startswith(('hook/', 'mcpServer/', 'item/command', 'item/file', 'item/mcp', 'item/tool')):
                    fail('Provider attempted an unsupported native action.')
                if method.startswith('codex/event/'):
                    # Legacy duplicate events are not an input source.
                    continue
                if method in ('item/started', 'item/completed', 'item/agentMessage/delta',
                              'thread/tokenUsage/updated', 'turn/completed', 'turn/started'):
                    if params.get('threadId') != self.thread:
                        fail('Unexpected provider thread identity.')
                    event_turn = params.get('turn', {}).get('id') if method.startswith('turn/') else params.get('turnId')
                    if event_turn != turn:
                        fail('Unexpected provider turn identity.')
                if method in ('item/started', 'item/completed'):
                    item = params.get('item', {})
                    kind = item.get('type')
                    if kind in ('reasoning', 'userMessage'):
                        continue
                    if kind != 'agentMessage':
                        fail('Provider emitted an unsupported native item.')
                    if item.get('phase') == 'commentary':
                        ignored_items.add(item.get('id'))
                        continue
                    if item.get('phase') not in (None, 'final_answer'):
                        fail('Unknown provider message phase.')
                    if method == 'item/started':
                        if message is not None:
                            fail('Provider emitted multiple answer messages.')
                        message = item.get('id')
                        if not isinstance(message, str) or not message:
                            fail('Invalid provider message identity.')
                        buffered = item.get('phase') is None
                    elif message != item.get('id') or completed or item.get('text') != raw:
                        fail('Provider final message did not match validated records.')
                    else:
                        completed = True
                        if not buffered:
                            records.finish()
                elif method == 'item/agentMessage/delta':
                    if params.get('itemId') in ignored_items:
                        continue
                    if message is None or params.get('itemId') != message or completed or not isinstance(params.get('delta'), str):
                        fail('Unexpected provider text sequence.')
                    if params['delta'] and phases['firstDeltaMs'] is None:
                        phases['firstDeltaMs'] = (time.monotonic() - dispatched) * 1000
                    raw += params['delta']
                    if len(raw.encode('utf-8')) > 1024 * 1024:
                        fail('Provider decision exceeded the size limit.')
                    if not buffered:
                        records.feed(params['delta'])
                elif method == 'thread/tokenUsage/updated':
                    candidate = params.get('tokenUsage', {}).get('last')
                    if not isinstance(candidate, dict) or any(type(v) is not int or v < 0 for v in candidate.values()):
                        fail('Invalid provider usage response.')
                    usage = candidate
                elif method == 'turn/completed':
                    final = params.get('turn', {})
                    if final.get('status') != 'completed' or final.get('error') is not None or not completed:
                        fail('Provider turn failed or did not complete; no retry was used.')
                    phases['turnCompletedMs'] = (time.monotonic() - dispatched) * 1000
                    if buffered:
                        records.feed(raw)
                    result = records.finish()
                    releasing = time.monotonic()
                    self._release_thread()
                    phases['cleanupMs'] = (time.monotonic() - releasing) * 1000
                    phases['totalMs'] = (time.monotonic() - started) * 1000
                    return result, usage, 'buffered' if buffered else 'incremental'

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None
        self.thread = None
        self.directory.cleanup()

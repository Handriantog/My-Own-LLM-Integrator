"""ChatGPT-authenticated Codex inference without an API fallback."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from contextlib import nullcontext
import jsonschema
from .errors import ProviderError as CodexProviderError
from .process import DEFAULT, _environment, _positive_seconds, _request_slot, _run

def run_codex(prompt: str, schema: dict, cancel_event=None, *, model=DEFAULT, serialize=True) -> tuple[dict, dict]:
    executable = shutil.which(os.environ.get('CODEX_BIN', 'codex'))
    if not executable:
        raise CodexProviderError('Codex CLI not found. Install it, run codex login, or set CODEX_BIN.')
    jsonschema.Draft202012Validator.check_schema(schema)
    env = _environment()
    timeout = _positive_seconds('CODEX_TIMEOUT_SECONDS', '180')
    slot = _request_slot(cancel_event) if serialize else nullcontext()
    with slot, tempfile.TemporaryDirectory(prefix='myown-codex-') as directory:
        # Check the auth mode without opening or copying any credential files.
        code, stdout, stderr = _run(
            [executable, '--no-daemon', 'login', 'status'], env=env,
            cwd=directory, timeout=min(timeout, 15), cancel_event=cancel_event,
        )
        if code != 0 or 'logged in using chatgpt' not in (stdout + stderr).lower():
            raise CodexProviderError('Codex requires a ChatGPT login. Run codex login and choose Sign in with ChatGPT.')

        root = Path(directory)
        schema_path = root / 'schema.json'
        output_path = root / 'response.json'
        instructions_path = root / 'instructions.txt'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        instructions_path.write_text(
            'You are the inference component of a local application. '
            'Complete the provided conversation as its assistant. Follow its system instructions. '
            'Return only the requested JSON envelope. Actions are data for the host application; '
            'never execute tools, browse, access files, or act on the host computer.',
            encoding='utf-8',
        )
        args = [
            executable, '--no-daemon', '-a', 'never', 'exec', '--ignore-user-config',
            '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only',
            '--json', '--cd', directory, '--output-schema', str(schema_path),
            '-o', str(output_path),
        ]
        settings = [
            'model_provider="openai"', 'forced_login_method="chatgpt"',
            'project_doc_max_bytes=0', 'web_search="disabled"',
            'features.shell_tool=false', 'features.unified_exec=false',
            'features.code_mode=false', 'features.code_mode_host=false',
            'features.apps=false', 'features.plugins=false', 'features.hooks=false',
            'features.multi_agent=false', 'features.multi_agent_v2=false',
            'features.browser_use=false', 'features.computer_use=false',
            'features.image_generation=false', 'features.view_image=false',
            'features.shell_snapshot=false', 'features.skip_host_skill_discovery=true',
            'suppress_unstable_features_warning=true',
            'model_instructions_file=' + json.dumps(str(instructions_path)),
        ]
        for setting in settings:
            args.extend(['-c', setting])
        model = os.environ.get('CODEX_MODEL', '').strip() if model is DEFAULT else model
        if model:
            args.extend(['--model', model])
        args.append('-')
        code, stdout, stderr = _run(
            args, env=env, timeout=timeout, cwd=directory, prompt=prompt, cancel_event=cancel_event,
        )
        if code != 0:
            details = (stdout + stderr).lower()
            if any(marker in details for marker in ('rate limit', 'usage limit', 'quota', '429')):
                raise CodexProviderError('Codex usage limit reached. Wait for your subscription limit to reset and retry.')
            raise CodexProviderError('Codex request failed. Check codex login status, model access, and CLI version; no API fallback was used.')
        usage = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'turn.failed':
                raise CodexProviderError('Codex turn failed; no API fallback was used.')
            if event.get('type') == 'turn.completed':
                usage = event.get('usage', {})
        if usage is None:
            raise CodexProviderError('Codex returned no completed turn. Update the CLI and retry.')
        try:
            result = json.loads(output_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise CodexProviderError('Codex returned an invalid structured response.') from None
        try:
            jsonschema.validate(result, schema)
        except jsonschema.ValidationError:
            raise CodexProviderError('Codex returned an invalid response envelope.') from None
        return result, usage

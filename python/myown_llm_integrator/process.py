"""Local, serialized Codex CLI execution. Credentials stay owned by Codex."""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import ProviderError as CodexProviderError

DEFAULT = object()
MAX_OUTPUT = 32 * 1024 * 1024


def _positive_seconds(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        raise CodexProviderError(f'{name} must be a positive number.') from None
    if not math.isfinite(value) or value <= 0:
        raise CodexProviderError(f'{name} must be a positive number.')
    return value


def _environment() -> dict[str, str]:
    # Retain OS/auth-store discovery and network configuration, not app secrets,
    # API credentials, parent thread IDs, or provider overrides.
    allowed = {
        'PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'CODEX_HOME',
        'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
        'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS',
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def default_lock_path(prefix='CODEX'):
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))).expanduser()
    if not state.is_absolute():
        raise CodexProviderError('XDG_STATE_HOME must be absolute.')
    return state / 'myown-llm-integrator' / (prefix.lower() + '.lock')


@contextmanager
def _request_slot(cancel_event=None, *, prefix='CODEX', label='Codex'):
    if os.name != 'posix':
        raise CodexProviderError(f'{label} provider requires macOS, Linux, or WSL.')
    import fcntl
    path = Path(os.environ.get(f'{prefix}_LOCK_PATH', str(default_lock_path(prefix)))).expanduser()
    if not path.is_absolute():
        raise CodexProviderError(f'{prefix}_LOCK_PATH must be an absolute path.')
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout = _positive_seconds(f'{prefix}_QUEUE_TIMEOUT_SECONDS', '600')
    started = time.monotonic()
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise CodexProviderError(f'{label} request cancelled.')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise CodexProviderError(f'{label} request queue timed out.')
                time.sleep(.025)
        yield time.monotonic() - started
    finally:
        os.close(descriptor)


def _run(args: list[str], *, env: dict[str, str], timeout: float,
         cwd: str, prompt: str | None = None, cancel_event=None,
         label='Codex', timeout_setting='CODEX_TIMEOUT_SECONDS',
         output_limit=MAX_OUTPUT) -> tuple[int, str, str]:
    input_bytes = (prompt or '').encode('utf-8')
    try:
        worker = Path(__file__).with_name('worker.py')
        process = subprocess.Popen(
            [sys.executable, '-I', str(worker), str(os.getpid()), *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env=env, cwd=cwd, start_new_session=True,
        )
    except OSError:
        raise CodexProviderError(f'Cannot start {label}. Check the configured CLI executable.') from None
    group_killed = False

    def kill_group():
        nonlocal group_killed
        if group_killed:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        group_killed = True

    try:
        deadline = time.monotonic() + timeout
        stdout, stderr = bytearray(), bytearray()
        size, offset = 0, 0
        # Drain output while sending input: either direction can fill its pipe.
        # Unlike communicate(), this enforces a cap before buffering all output.
        with selectors.DefaultSelector() as selector:
            for stream, buffer in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, buffer)
            if input_bytes:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while selector.get_map() or process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise CodexProviderError(f'{label} request cancelled.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args[0], timeout)
                if process.poll() is not None:
                    # Descendants can keep pipes open after the CLI has exited.
                    kill_group()
                for key, _ in selector.select(min(.1, remaining)):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            offset += os.write(key.fd, input_bytes[offset:offset + 4096])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            offset = len(input_bytes)
                        if offset == len(input_bytes):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        size += len(chunk)
                        if size > output_limit:
                            raise CodexProviderError(f'{label} response exceeded the output limit.')
                        key.data.extend(chunk)
        return process.returncode, stdout.decode('utf-8'), stderr.decode('utf-8')
    except subprocess.TimeoutExpired:
        raise CodexProviderError(f'{label} request timed out. Reduce the prompt or increase {timeout_setting}.') from None
    finally:
        # Cleanup applies to success as well as failure. Do not re-buffer output
        # during cleanup or wait for pipes held by an escaped descendant.
        kill_group()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        process.wait()

"""Reusable local CLI inference. Authentication remains owned by each CLI."""
from .errors import ProviderError
from .registry import PROVIDERS, get_provider, is_agent_provider, model_name
from .transport import run_agent, validate_provider
from .codex import run_codex

__all__ = ['ProviderError', 'PROVIDERS', 'get_provider', 'is_agent_provider',
           'model_name', 'run_agent', 'run_codex', 'validate_provider']

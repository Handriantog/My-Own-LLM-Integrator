class ProviderError(RuntimeError):
    """Safe error for callers; never includes raw CLI output or credentials."""

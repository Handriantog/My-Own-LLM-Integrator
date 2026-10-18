"""Local no-inference smoke. Reports booleans only, never config/account values.

Run: uv run --project python --frozen python scripts/check-provider-isolation.py
Starts an ephemeral thread, but never sends turn/start or executes model actions.
"""
import json
import time
from myown_llm_integrator.provider_session import ProviderSession, verify_config

started = time.monotonic()
session = ProviderSession()
try:
    verify_config(session.effective_config)
    session._start_thread()
    servers = session.server.rpc('mcpServerStatus/list', {'limit': 100})
    if (servers.get('nextCursor') is not None or any(
        item.get('runtimeStatus') is not None or item.get('tools') or item.get('resources') or item.get('resourceTemplates')
        for item in servers.get('data', []))):
        raise RuntimeError('Provider MCP capabilities were not empty.')
    print(json.dumps({'restrictedConfigVerified': True, 'chatgptAuthVerified': True,
                      'ephemeralReadOnlyThreadVerified': True, 'mcpCapabilitiesEmpty': True,
                      'inferenceTurns': 0, 'elapsedMs': round((time.monotonic()-started)*1000, 1)}))
finally:
    session.close()

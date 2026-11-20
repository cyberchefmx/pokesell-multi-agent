"""PokeSell Ops (`pokesell_agent`) — Google ADK Multi-Agent Package Root.

Exports `agent`, `root_agent`, and `app` for seamless execution with:
- `adk web .`
- `adk run pokesell_agent`
- `adk api_server .`
- `agents-cli playground`
"""

from pokesell_agent import agent
from pokesell_agent.agent import app, root_agent

__all__ = ["agent", "app", "root_agent"]

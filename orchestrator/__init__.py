"""The agent orchestrator.

Discovers the agents declared in `registry.yaml` and calls them over the
`agentcall/v1` contract (see AGENT_PROTOCOL.md).

This package never imports agent code. Every call crosses a process
boundary, so each agent keeps its own dependency set and its own working
directory. Deleting this package leaves every agent intact and runnable.
"""

from orchestrator.contract import PROTOCOL, CallError, CallRequest, CallResult, Usage
from orchestrator.discovery import Registry, load_registry
from orchestrator.manifest import AgentManifest, Capability
from orchestrator.runner import call, describe

__all__ = [
    "PROTOCOL",
    "AgentManifest",
    "CallError",
    "CallRequest",
    "CallResult",
    "Capability",
    "Registry",
    "Usage",
    "call",
    "describe",
    "load_registry",
]

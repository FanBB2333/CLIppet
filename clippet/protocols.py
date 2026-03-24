"""Protocol definitions for CLIppet adapters."""

from typing import Protocol, runtime_checkable

from clippet.models import AgentRequest, AgentResult


@runtime_checkable
class ClippetAdapter(Protocol):
    """Protocol that all CLIppet adapters must implement."""

    @property
    def agent_name(self) -> str:
        """Return the name of the agent this adapter interfaces with."""
        ...

    def run(self, request: AgentRequest) -> AgentResult:
        """Execute the agent synchronously with the given request."""
        ...

    async def run_async(self, request: AgentRequest) -> AgentResult:
        """Execute the agent asynchronously with the given request."""
        ...

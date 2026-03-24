"""Multi-agent orchestration dispatcher for CLIppet."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from clippet.models import AgentRequest, AgentResult

if TYPE_CHECKING:
    from clippet.protocols import ClippetAdapter


class ClippetRunner:
    """Multi-agent orchestration dispatcher.
    
    Manages registration and execution of multiple CLIppet adapters,
    supporting both synchronous and asynchronous execution patterns.
    
    Example:
        runner = ClippetRunner(max_workers=4)
        runner.register("claude", ClaudeAdapter())
        runner.register("codex", CodexAdapter())
        
        # Single execution
        result = runner.execute("claude", request)
        
        # Parallel execution
        results = runner.execute_parallel({
            "claude": request1,
            "codex": request2,
        })
    """

    def __init__(self, max_workers: int = 4) -> None:
        """Initialize the ClippetRunner.
        
        Args:
            max_workers: Maximum number of worker threads for parallel execution.
        """
        self._adapters: dict[str, "ClippetAdapter"] = {}
        self._max_workers = max_workers

    def register(self, name: str, adapter: "ClippetAdapter") -> None:
        """Register an adapter by name.
        
        Args:
            name: Unique name for the adapter.
            adapter: The adapter instance to register.
            
        Raises:
            ValueError: If an adapter with the given name is already registered.
        """
        if name in self._adapters:
            raise ValueError(f"Adapter '{name}' is already registered")
        self._adapters[name] = adapter

    def unregister(self, name: str) -> None:
        """Remove an adapter by name.
        
        Args:
            name: Name of the adapter to remove.
            
        Raises:
            KeyError: If no adapter with the given name is registered.
        """
        if name not in self._adapters:
            raise KeyError(f"Adapter '{name}' is not registered")
        del self._adapters[name]

    def get_adapter(self, name: str) -> "ClippetAdapter":
        """Get an adapter by name.
        
        Args:
            name: Name of the adapter to retrieve.
            
        Returns:
            The registered adapter instance.
            
        Raises:
            KeyError: If no adapter with the given name is registered.
        """
        if name not in self._adapters:
            raise KeyError(f"Adapter '{name}' is not registered")
        return self._adapters[name]

    def list_adapters(self) -> list[str]:
        """Return list of registered adapter names.
        
        Returns:
            List of registered adapter names.
        """
        return list(self._adapters.keys())

    def execute(self, name: str, request: AgentRequest) -> AgentResult:
        """Execute a single adapter synchronously.
        
        Args:
            name: Name of the adapter to execute.
            request: The agent request to execute.
            
        Returns:
            AgentResult from the adapter execution.
        """
        try:
            adapter = self.get_adapter(name)
            return adapter.run(request)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                error_message=f"Execution failed: {e}",
            )

    async def execute_async(self, name: str, request: AgentRequest) -> AgentResult:
        """Execute a single adapter asynchronously.
        
        Args:
            name: Name of the adapter to execute.
            request: The agent request to execute.
            
        Returns:
            AgentResult from the adapter execution.
        """
        try:
            adapter = self.get_adapter(name)
            return await adapter.run_async(request)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                error_message=f"Async execution failed: {e}",
            )

    def execute_parallel(
        self, tasks: dict[str, AgentRequest]
    ) -> dict[str, AgentResult]:
        """Execute multiple adapters concurrently using ThreadPoolExecutor.
        
        Args:
            tasks: Dict mapping adapter_name -> request.
            
        Returns:
            Dict mapping adapter_name -> result.
        """
        results: dict[str, AgentResult] = {}
        
        if not tasks:
            return results

        def run_adapter(name: str, request: AgentRequest) -> tuple[str, AgentResult]:
            """Execute a single adapter and return name with result."""
            try:
                adapter = self.get_adapter(name)
                result = adapter.run(request)
                return name, result
            except Exception as e:
                return name, AgentResult(
                    raw_output="",
                    is_success=False,
                    error_message=f"Parallel execution failed for '{name}': {e}",
                )

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            # Track which future corresponds to which adapter name
            future_to_name = {
                executor.submit(run_adapter, name, request): name
                for name, request in tasks.items()
            }
            
            for future in future_to_name:
                name = future_to_name[future]
                try:
                    _, result = future.result()
                    results[name] = result
                except Exception as e:
                    # This shouldn't happen since run_adapter catches exceptions,
                    # but handle it defensively with a synthetic error result
                    results[name] = AgentResult(
                        raw_output="",
                        is_success=False,
                        error_message=f"Unexpected executor error for '{name}': {e}",
                    )

        return results

    async def execute_parallel_async(
        self, tasks: dict[str, AgentRequest]
    ) -> dict[str, AgentResult]:
        """Execute multiple adapters concurrently using asyncio.gather.
        
        Args:
            tasks: Dict mapping adapter_name -> request.
            
        Returns:
            Dict mapping adapter_name -> result.
        """
        results: dict[str, AgentResult] = {}
        
        if not tasks:
            return results

        async def run_adapter_async(
            name: str, request: AgentRequest
        ) -> tuple[str, AgentResult]:
            """Execute a single adapter asynchronously and return name with result."""
            try:
                adapter = self.get_adapter(name)
                result = await adapter.run_async(request)
                return name, result
            except Exception as e:
                return name, AgentResult(
                    raw_output="",
                    is_success=False,
                    error_message=f"Async parallel execution failed for '{name}': {e}",
                )

        # Track adapter names in order to map results back
        adapter_names = list(tasks.keys())
        
        coroutines = [
            run_adapter_async(name, request)
            for name, request in tasks.items()
        ]
        
        completed = await asyncio.gather(*coroutines, return_exceptions=True)
        
        for idx, item in enumerate(completed):
            name = adapter_names[idx]
            if isinstance(item, Exception):
                # This shouldn't happen since run_adapter_async catches exceptions,
                # but handle it defensively with a synthetic error result
                results[name] = AgentResult(
                    raw_output="",
                    is_success=False,
                    error_message=f"Unexpected async error for '{name}': {item}",
                )
            else:
                _, result = item
                results[name] = result

        return results

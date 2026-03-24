"""Unit tests for CLIppet orchestrator."""

import pytest

from clippet.models import AgentRequest, AgentResult
from clippet.orchestrator import ClippetRunner


class MockAdapter:
    """Mock adapter for testing the orchestrator."""
    
    def __init__(self, name: str = "mock"):
        self._name = name
    
    @property
    def agent_name(self) -> str:
        return self._name
    
    def run(self, request: AgentRequest) -> AgentResult:
        return AgentResult(
            raw_output=f"mock result from {self._name}",
            is_success=True,
            execution_time=0.1,
            steps_count=1,
        )
    
    async def run_async(self, request: AgentRequest) -> AgentResult:
        return AgentResult(
            raw_output=f"mock async result from {self._name}",
            is_success=True,
            execution_time=0.1,
            steps_count=1,
        )


class FailingMockAdapter:
    """Mock adapter that always fails."""
    
    @property
    def agent_name(self) -> str:
        return "failing-mock"
    
    def run(self, request: AgentRequest) -> AgentResult:
        return AgentResult(
            raw_output="",
            is_success=False,
            execution_time=0.05,
            error_message="Simulated failure",
        )
    
    async def run_async(self, request: AgentRequest) -> AgentResult:
        return AgentResult(
            raw_output="",
            is_success=False,
            execution_time=0.05,
            error_message="Simulated async failure",
        )


class TestClippetRunner:
    """Tests for ClippetRunner orchestrator."""

    def test_register_and_list(self):
        """Test registering adapters and listing them."""
        runner = ClippetRunner()
        
        adapter1 = MockAdapter("mock1")
        adapter2 = MockAdapter("mock2")
        
        runner.register("adapter-1", adapter1)
        runner.register("adapter-2", adapter2)
        
        adapters = runner.list_adapters()
        
        assert "adapter-1" in adapters
        assert "adapter-2" in adapters
        assert len(adapters) == 2

    def test_register_duplicate(self):
        """Test that registering duplicate name raises ValueError."""
        runner = ClippetRunner()
        
        adapter1 = MockAdapter("mock1")
        adapter2 = MockAdapter("mock2")
        
        runner.register("my-adapter", adapter1)
        
        with pytest.raises(ValueError, match="already registered"):
            runner.register("my-adapter", adapter2)

    def test_unregister(self):
        """Test unregistering an adapter."""
        runner = ClippetRunner()
        
        adapter = MockAdapter()
        runner.register("to-remove", adapter)
        
        assert "to-remove" in runner.list_adapters()
        
        runner.unregister("to-remove")
        
        assert "to-remove" not in runner.list_adapters()

    def test_unregister_unknown(self):
        """Test that unregistering unknown adapter raises KeyError."""
        runner = ClippetRunner()
        
        with pytest.raises(KeyError, match="not registered"):
            runner.unregister("nonexistent")

    def test_get_adapter(self):
        """Test retrieving an adapter by name."""
        runner = ClippetRunner()
        
        adapter = MockAdapter("test-mock")
        runner.register("my-mock", adapter)
        
        retrieved = runner.get_adapter("my-mock")
        
        assert retrieved is adapter
        assert retrieved.agent_name == "test-mock"

    def test_get_adapter_unknown(self):
        """Test that getting unknown adapter raises KeyError."""
        runner = ClippetRunner()
        
        with pytest.raises(KeyError, match="not registered"):
            runner.get_adapter("unknown")

    def test_execute(self):
        """Test executing a single adapter."""
        runner = ClippetRunner()
        
        adapter = MockAdapter("exec-mock")
        runner.register("exec-adapter", adapter)
        
        request = AgentRequest(
            task_prompt="Test task",
            workspace_dir="/tmp",
        )
        
        result = runner.execute("exec-adapter", request)
        
        assert isinstance(result, AgentResult)
        assert result.is_success is True
        assert "mock result from exec-mock" in result.raw_output

    def test_execute_unknown_adapter(self):
        """Test that executing unknown adapter returns error result."""
        runner = ClippetRunner()
        
        request = AgentRequest(
            task_prompt="Test task",
            workspace_dir="/tmp",
        )
        
        # Execute method catches KeyError and returns error result
        result = runner.execute("nonexistent", request)
        
        assert result.is_success is False
        assert "nonexistent" in result.error_message

    def test_execute_parallel(self):
        """Test parallel execution of multiple adapters."""
        runner = ClippetRunner(max_workers=4)
        
        adapter1 = MockAdapter("parallel1")
        adapter2 = MockAdapter("parallel2")
        
        runner.register("p1", adapter1)
        runner.register("p2", adapter2)
        
        request1 = AgentRequest(task_prompt="Task 1", workspace_dir="/tmp")
        request2 = AgentRequest(task_prompt="Task 2", workspace_dir="/tmp")
        
        results = runner.execute_parallel({
            "p1": request1,
            "p2": request2,
        })
        
        assert len(results) == 2
        assert "p1" in results
        assert "p2" in results
        
        assert results["p1"].is_success is True
        assert results["p2"].is_success is True
        assert "parallel1" in results["p1"].raw_output
        assert "parallel2" in results["p2"].raw_output

    def test_execute_parallel_empty(self):
        """Test parallel execution with empty tasks dict."""
        runner = ClippetRunner()
        
        results = runner.execute_parallel({})
        
        assert results == {}

    def test_execute_parallel_with_failure(self):
        """Test parallel execution with one failing adapter."""
        runner = ClippetRunner()
        
        good_adapter = MockAdapter("good")
        failing_adapter = FailingMockAdapter()
        
        runner.register("good", good_adapter)
        runner.register("failing", failing_adapter)
        
        request = AgentRequest(task_prompt="Test", workspace_dir="/tmp")
        
        results = runner.execute_parallel({
            "good": request,
            "failing": request,
        })
        
        assert results["good"].is_success is True
        assert results["failing"].is_success is False
        assert results["failing"].error_message == "Simulated failure"


@pytest.mark.asyncio
class TestClippetRunnerAsync:
    """Async tests for ClippetRunner orchestrator."""

    async def test_execute_async(self):
        """Test async execution of a single adapter."""
        runner = ClippetRunner()
        
        adapter = MockAdapter("async-mock")
        runner.register("async-adapter", adapter)
        
        request = AgentRequest(
            task_prompt="Async test task",
            workspace_dir="/tmp",
        )
        
        result = await runner.execute_async("async-adapter", request)
        
        assert isinstance(result, AgentResult)
        assert result.is_success is True
        assert "mock async result from async-mock" in result.raw_output

    async def test_execute_parallel_async(self):
        """Test async parallel execution of multiple adapters."""
        runner = ClippetRunner()
        
        adapter1 = MockAdapter("async1")
        adapter2 = MockAdapter("async2")
        
        runner.register("a1", adapter1)
        runner.register("a2", adapter2)
        
        request1 = AgentRequest(task_prompt="Async Task 1", workspace_dir="/tmp")
        request2 = AgentRequest(task_prompt="Async Task 2", workspace_dir="/tmp")
        
        results = await runner.execute_parallel_async({
            "a1": request1,
            "a2": request2,
        })
        
        assert len(results) == 2
        assert results["a1"].is_success is True
        assert results["a2"].is_success is True

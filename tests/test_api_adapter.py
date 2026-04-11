"""Unit tests for OpenAIAdapter (API-based adapter).

All tests mock the OpenAI SDK so no real API calls are made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clippet.models import AgentRequest

# ---------------------------------------------------------------------------
# Guard: skip entire module if openai is not installed
# ---------------------------------------------------------------------------

openai = pytest.importorskip("openai", reason="openai package not installed")

from clippet.adapters.api import OpenAIAdapter, _require_openai  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — fake ChatCompletion / ChatCompletionChunk objects
# ---------------------------------------------------------------------------


def _fake_completion(content: str = "Hello!", model: str = "gpt-4o") -> MagicMock:
    """Return a mock that behaves like ``openai.types.chat.ChatCompletion``."""
    message = SimpleNamespace(role="assistant", content=content)
    choice = SimpleNamespace(index=0, message=message, finish_reason="stop")
    completion = MagicMock()
    completion.choices = [choice]
    completion.model = model
    completion.model_dump_json.return_value = (
        f'{{"id":"chatcmpl-test","model":"{model}",'
        f'"choices":[{{"message":{{"content":"{content}"}}}}]}}'
    )
    return completion


def _fake_chunk(content: str = "Hi") -> MagicMock:
    """Return a mock that looks like a ``ChatCompletionChunk``."""
    delta = SimpleNamespace(content=content, role=None)
    choice = SimpleNamespace(index=0, delta=delta, finish_reason=None)
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


# ---------------------------------------------------------------------------
# Construction & properties
# ---------------------------------------------------------------------------


class TestOpenAIAdapterConstruction:
    """Test adapter creation and basic properties."""

    def test_agent_name(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        assert adapter.agent_name == "openai-api"

    def test_default_model(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        assert adapter.model == "gpt-4o"

    def test_custom_model(self):
        adapter = OpenAIAdapter(model="gpt-3.5-turbo", api_key="sk-test")
        assert adapter.model == "gpt-3.5-turbo"

    def test_skills_stored(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            skills=["Skill A", "Skill B"],
        )
        assert adapter.skills == ["Skill A", "Skill B"]

    def test_skills_default_empty(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        assert adapter.skills == []

    def test_extra_create_kwargs(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            extra_create_kwargs={"top_p": 0.9},
        )
        assert adapter.extra_create_kwargs == {"top_p": 0.9}

    def test_reserved_fields(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            system_prompt_template="Template {skills}",
            agent_persona="claude-code",
        )
        assert adapter.system_prompt_template == "Template {skills}"
        assert adapter.agent_persona == "claude-code"


# ---------------------------------------------------------------------------
# Prompt / message building
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    """Test system prompt and message assembly."""

    def test_default_system_prompt_no_skills(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        prompt = adapter.build_system_prompt()
        assert prompt == "You are a helpful coding assistant."

    def test_system_prompt_with_adapter_skills(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            skills=["Always use type hints"],
        )
        prompt = adapter.build_system_prompt()
        assert "# Skills" in prompt
        assert "Always use type hints" in prompt

    def test_system_prompt_with_extra_skills(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        prompt = adapter.build_system_prompt(extra_skills=["Follow PEP8"])
        assert "Follow PEP8" in prompt

    def test_system_prompt_combines_adapter_and_extra_skills(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            skills=["Skill A"],
        )
        prompt = adapter.build_system_prompt(extra_skills=["Skill B"])
        assert "Skill A" in prompt
        assert "Skill B" in prompt

    def test_system_prompt_template_replaces_skills(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            system_prompt_template="You are Codex.{skills}",
            skills=["Use Python 3.12"],
        )
        prompt = adapter.build_system_prompt()
        assert prompt.startswith("You are Codex.")
        assert "Use Python 3.12" in prompt
        assert "You are a helpful coding assistant" not in prompt

    def test_build_messages(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Fix the bug", workspace_dir="/tmp")
        messages = adapter.build_messages(request)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Fix the bug"


# ---------------------------------------------------------------------------
# _create_kwargs
# ---------------------------------------------------------------------------


class TestCreateKwargs:
    """Test the internal _create_kwargs method."""

    def test_defaults(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Hello", workspace_dir="/tmp")
        kwargs = adapter._create_kwargs(request)

        assert kwargs["model"] == "gpt-4o"
        assert kwargs["stream"] is False
        assert len(kwargs["messages"]) == 2
        assert "temperature" not in kwargs
        assert "max_tokens" not in kwargs

    def test_request_model_overrides(self):
        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        request = AgentRequest(
            task_prompt="Hello",
            workspace_dir="/tmp",
            model="gpt-3.5-turbo",
        )
        kwargs = adapter._create_kwargs(request)
        assert kwargs["model"] == "gpt-3.5-turbo"

    def test_temperature_and_max_tokens(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            temperature=0.7,
            max_tokens=1024,
        )
        request = AgentRequest(task_prompt="Hello", workspace_dir="/tmp")
        kwargs = adapter._create_kwargs(request)
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 1024

    def test_extra_kwargs_forwarded(self):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            extra_create_kwargs={"top_p": 0.9, "presence_penalty": 0.5},
        )
        request = AgentRequest(task_prompt="Hello", workspace_dir="/tmp")
        kwargs = adapter._create_kwargs(request)
        assert kwargs["top_p"] == 0.9
        assert kwargs["presence_penalty"] == 0.5

    def test_stream_flag(self):
        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Hello", workspace_dir="/tmp")
        kwargs = adapter._create_kwargs(request, stream=True)
        assert kwargs["stream"] is True


# ---------------------------------------------------------------------------
# Synchronous call
# ---------------------------------------------------------------------------


class TestSyncCall:
    """Test synchronous call / run methods."""

    @patch("clippet.adapters.api.OpenAI")
    def test_call_returns_completion(self, MockOpenAI):
        mock_client = MagicMock()
        fake = _fake_completion("Done!")
        mock_client.chat.completions.create.return_value = fake
        MockOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Fix it", workspace_dir="/tmp")
        result = adapter.call(request)

        assert result is fake
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["stream"] is False

    @patch("clippet.adapters.api.OpenAI")
    def test_run_returns_agent_result_success(self, MockOpenAI):
        mock_client = MagicMock()
        fake = _fake_completion("All fixed")
        mock_client.chat.completions.create.return_value = fake
        MockOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Fix it", workspace_dir="/tmp")
        result = adapter.run(request)

        assert result.is_success is True
        assert result.execution_time > 0
        assert result.steps_count == 1
        assert "chatcmpl-test" in result.raw_output

    @patch("clippet.adapters.api.OpenAI")
    def test_run_handles_api_error(self, MockOpenAI):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        MockOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Fix it", workspace_dir="/tmp")
        result = adapter.run(request)

        assert result.is_success is False
        assert "API down" in result.error_message


# ---------------------------------------------------------------------------
# Streaming call
# ---------------------------------------------------------------------------


class TestStreamCall:
    """Test synchronous streaming."""

    @patch("clippet.adapters.api.OpenAI")
    def test_call_stream_yields_chunks(self, MockOpenAI):
        mock_client = MagicMock()
        chunks = [_fake_chunk("Hello"), _fake_chunk(" world")]
        mock_client.chat.completions.create.return_value = iter(chunks)
        MockOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Say hi", workspace_dir="/tmp")
        collected = list(adapter.call_stream(request))

        assert len(collected) == 2
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["stream"] is True


# ---------------------------------------------------------------------------
# Async call
# ---------------------------------------------------------------------------


class TestAsyncCall:
    """Test async call / run methods."""

    @pytest.mark.asyncio
    @patch("clippet.adapters.api.AsyncOpenAI")
    async def test_call_async_returns_completion(self, MockAsyncOpenAI):
        mock_client = MagicMock()
        fake = _fake_completion("Async done")
        mock_client.chat.completions.create = AsyncMock(return_value=fake)
        MockAsyncOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Async fix", workspace_dir="/tmp")
        result = await adapter.call_async(request)

        assert result is fake

    @pytest.mark.asyncio
    @patch("clippet.adapters.api.AsyncOpenAI")
    async def test_run_async_returns_agent_result(self, MockAsyncOpenAI):
        mock_client = MagicMock()
        fake = _fake_completion("Async result")
        mock_client.chat.completions.create = AsyncMock(return_value=fake)
        MockAsyncOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Async fix", workspace_dir="/tmp")
        result = await adapter.run_async(request)

        assert result.is_success is True
        assert result.steps_count == 1

    @pytest.mark.asyncio
    @patch("clippet.adapters.api.AsyncOpenAI")
    async def test_run_async_handles_error(self, MockAsyncOpenAI):
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=RuntimeError("Timeout")
        )
        MockAsyncOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Fail", workspace_dir="/tmp")
        result = await adapter.run_async(request)

        assert result.is_success is False
        assert "Timeout" in result.error_message


# ---------------------------------------------------------------------------
# Async streaming
# ---------------------------------------------------------------------------


class TestAsyncStreamCall:
    """Test async streaming."""

    @pytest.mark.asyncio
    @patch("clippet.adapters.api.AsyncOpenAI")
    async def test_call_stream_async_yields_chunks(self, MockAsyncOpenAI):
        mock_client = MagicMock()
        chunks = [_fake_chunk("A"), _fake_chunk("B"), _fake_chunk("C")]

        async def fake_aiter():
            for c in chunks:
                yield c

        mock_client.chat.completions.create = AsyncMock(
            return_value=fake_aiter()
        )
        MockAsyncOpenAI.return_value = mock_client

        adapter = OpenAIAdapter(api_key="sk-test")
        request = AgentRequest(task_prompt="Stream", workspace_dir="/tmp")
        collected = []
        async for chunk in adapter.call_stream_async(request):
            collected.append(chunk)

        assert len(collected) == 3


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------


class TestClientInit:
    """Test lazy client initialization and configuration."""

    @patch("clippet.adapters.api.OpenAI")
    def test_api_key_passed_to_client(self, MockOpenAI):
        adapter = OpenAIAdapter(api_key="sk-my-key")
        adapter._get_sync_client()
        MockOpenAI.assert_called_once_with(api_key="sk-my-key")

    @patch("clippet.adapters.api.OpenAI")
    def test_base_url_passed_to_client(self, MockOpenAI):
        adapter = OpenAIAdapter(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
        )
        adapter._get_sync_client()
        MockOpenAI.assert_called_once_with(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
        )

    @patch("clippet.adapters.api.OpenAI")
    def test_client_cached(self, MockOpenAI):
        adapter = OpenAIAdapter(api_key="sk-test")
        c1 = adapter._get_sync_client()
        c2 = adapter._get_sync_client()
        assert c1 is c2
        MockOpenAI.assert_called_once()

    @patch("clippet.adapters.api.AsyncOpenAI")
    def test_async_client_cached(self, MockAsyncOpenAI):
        adapter = OpenAIAdapter(api_key="sk-test")
        c1 = adapter._get_async_client()
        c2 = adapter._get_async_client()
        assert c1 is c2
        MockAsyncOpenAI.assert_called_once()


# ---------------------------------------------------------------------------
# _require_openai guard
# ---------------------------------------------------------------------------


class TestRequireOpenai:
    """Test the import guard function."""

    def test_does_not_raise_when_available(self):
        # If we got this far, openai IS importable
        _require_openai()  # should not raise

    @patch("clippet.adapters.api._OPENAI_AVAILABLE", False)
    def test_raises_when_unavailable(self):
        with pytest.raises(ImportError, match="openai"):
            _require_openai()

    @patch("clippet.adapters.api._OPENAI_AVAILABLE", False)
    def test_adapter_init_raises_when_unavailable(self):
        with pytest.raises(ImportError, match="openai"):
            OpenAIAdapter(api_key="sk-test")

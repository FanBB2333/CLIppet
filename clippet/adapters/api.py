"""OpenAI API adapter for CLIppet.

Provides a thin wrapper around the OpenAI Python SDK that fits into the
CLIppet adapter ecosystem.  Unlike the subprocess-based adapters this one
calls a remote API directly.

Features:
  * Skills injection — skill texts are wrapped in ``<skill_content>`` tags
    (matching the format used by Claude Code / OpenCode) and appended to the
    system message so the model "knows" them.
  * Streaming support — ``call_stream`` / ``call_stream_async`` yield native
    ``ChatCompletionChunk`` objects.
  * Agent persona — the ``agent_persona`` parameter selects a persona-specific
    base system prompt so the model behaves like a particular coding agent.
"""

from __future__ import annotations

import time
from typing import Any, Generator, AsyncGenerator, Iterator

from clippet.adapters.base import BaseAdapter
from clippet.adapters.personas import PERSONA_PROMPTS, format_skill_block
from clippet.models import AgentRequest, AgentResult

try:
    import openai
    from openai import OpenAI, AsyncOpenAI
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OPENAI_AVAILABLE = False


def _require_openai() -> None:
    if not _OPENAI_AVAILABLE:
        raise ImportError(
            "The 'openai' package is required for OpenAIAdapter. "
            "Install it with: pip install openai"
        )


class OpenAIAdapter(BaseAdapter):
    """Adapter that calls the OpenAI Chat Completions API.

    This adapter inherits from :class:`BaseAdapter` (the unified base for all
    CLIppet adapters) and therefore implements ``run`` / ``run_async`` so it
    can be registered with :class:`ClippetRunner` just like any subprocess
    adapter.

    Additionally it exposes ``call`` / ``call_async`` that return native
    ``ChatCompletion`` objects, and ``call_stream`` / ``call_stream_async``
    for streaming responses.

    Args:
        model: OpenAI model name (e.g. ``"gpt-4o"``).
        api_key: API key.  Falls back to ``OPENAI_API_KEY`` env var.
        base_url: Optional custom base URL (for proxies / compatible APIs).
        skills: A list of skill text snippets to inject into the system
            prompt on every call.  Each skill is wrapped in a
            ``<skill_content>`` XML tag in the final prompt.
        system_prompt_template: When set, the entire system prompt is
            replaced with this template.  The placeholder ``{skills}``
            is substituted with the formatted skill block.
        agent_persona: A short identifier (e.g. ``"claude-code"``,
            ``"codex"``, ``"opencode"``) that selects a persona-specific
            base system prompt.  See :data:`PERSONA_PROMPTS` for the
            available personas.  When set, ``default_system_prompt`` is
            ignored in favour of the persona's prompt.
        default_system_prompt: Base system prompt used when neither
            ``system_prompt_template`` nor ``agent_persona`` is set.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in the response.
        extra_create_kwargs: Additional keyword arguments forwarded to
            ``client.chat.completions.create()``.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        skills: list[str] | None = None,
        system_prompt_template: str | None = None,
        agent_persona: str | None = None,
        default_system_prompt: str = "You are a helpful coding assistant.",
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_create_kwargs: dict[str, Any] | None = None,
    ) -> None:
        _require_openai()

        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.skills = list(skills) if skills else []
        self.system_prompt_template = system_prompt_template
        self.agent_persona = agent_persona
        self.default_system_prompt = default_system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_create_kwargs = dict(extra_create_kwargs or {})

        # Lazy-init clients
        self._sync_client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None

    # -- Properties ----------------------------------------------------------

    @property
    def agent_name(self) -> str:
        return "openai-api"

    # -- Client helpers ------------------------------------------------------

    def _get_sync_client(self) -> "OpenAI":
        if self._sync_client is None:
            kwargs: dict[str, Any] = {}
            if self.api_key is not None:
                kwargs["api_key"] = self.api_key
            if self.base_url is not None:
                kwargs["base_url"] = self.base_url
            self._sync_client = OpenAI(**kwargs)
        return self._sync_client

    def _get_async_client(self) -> "AsyncOpenAI":
        if self._async_client is None:
            kwargs: dict[str, Any] = {}
            if self.api_key is not None:
                kwargs["api_key"] = self.api_key
            if self.base_url is not None:
                kwargs["base_url"] = self.base_url
            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    # -- Prompt assembly -----------------------------------------------------

    def build_system_prompt(
        self,
        extra_skills: list[str] | None = None,
    ) -> str:
        """Build the full system prompt with skills injected.

        Skills are wrapped in ``<skill_content name="skill_N">`` XML tags,
        matching the format used by Claude Code and OpenCode when loading
        skills into context.

        The base system prompt is selected from (highest priority first):
        1. ``system_prompt_template`` (with ``{skills}`` placeholder)
        2. ``agent_persona`` (looked up in :data:`PERSONA_PROMPTS`)
        3. ``default_system_prompt``

        Args:
            extra_skills: Per-request skill texts appended to the
                adapter-level ``self.skills``.

        Returns:
            The assembled system prompt string.
        """

        all_skills = list(self.skills)
        if extra_skills:
            all_skills.extend(extra_skills)

        skills_block = format_skill_block(all_skills)

        # --- choose base prompt ---
        if self.system_prompt_template is not None:
            return self.system_prompt_template.replace("{skills}", skills_block)

        if self.agent_persona and self.agent_persona in PERSONA_PROMPTS:
            base = PERSONA_PROMPTS[self.agent_persona]
        else:
            base = self.default_system_prompt

        return base + skills_block

    def build_messages(
        self,
        request: AgentRequest,
        extra_skills: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Build the ``messages`` list for the Chat Completions API.

        Args:
            request: The agent request.
            extra_skills: Additional per-request skills.

        Returns:
            A list of message dicts ready for the API.
        """

        system_prompt = self.build_system_prompt(extra_skills=extra_skills)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": request.task_prompt},
        ]
        return messages

    # -- Common create-kwargs ------------------------------------------------

    def _create_kwargs(
        self,
        request: AgentRequest,
        stream: bool = False,
        extra_skills: list[str] | None = None,
    ) -> dict[str, Any]:
        effective_model = request.model if request.model else self.model
        messages = self.build_messages(request, extra_skills=extra_skills)

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "stream": stream,
        }

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        kwargs.update(self.extra_create_kwargs)
        return kwargs

    # -- Synchronous call (returns native ChatCompletion) --------------------

    def call(
        self,
        request: AgentRequest,
        extra_skills: list[str] | None = None,
    ) -> "ChatCompletion":
        """Call the Chat Completions API and return a native ``ChatCompletion``.

        This is a thin wrapper — the return value is the raw OpenAI object.
        """

        client = self._get_sync_client()
        kwargs = self._create_kwargs(request, stream=False, extra_skills=extra_skills)
        return client.chat.completions.create(**kwargs)

    # -- Streaming call ------------------------------------------------------

    def call_stream(
        self,
        request: AgentRequest,
        extra_skills: list[str] | None = None,
    ) -> Iterator["ChatCompletionChunk"]:
        """Stream the Chat Completions API response.

        Yields native ``ChatCompletionChunk`` objects.
        """

        client = self._get_sync_client()
        kwargs = self._create_kwargs(request, stream=True, extra_skills=extra_skills)
        yield from client.chat.completions.create(**kwargs)

    # -- Async call ----------------------------------------------------------

    async def call_async(
        self,
        request: AgentRequest,
        extra_skills: list[str] | None = None,
    ) -> "ChatCompletion":
        """Async version of :meth:`call`."""

        client = self._get_async_client()
        kwargs = self._create_kwargs(request, stream=False, extra_skills=extra_skills)
        return await client.chat.completions.create(**kwargs)

    # -- Async streaming call ------------------------------------------------

    async def call_stream_async(
        self,
        request: AgentRequest,
        extra_skills: list[str] | None = None,
    ) -> AsyncGenerator["ChatCompletionChunk", None]:
        """Async streaming version of :meth:`call_stream`."""

        client = self._get_async_client()
        kwargs = self._create_kwargs(request, stream=True, extra_skills=extra_skills)
        response = await client.chat.completions.create(**kwargs)
        async for chunk in response:
            yield chunk

    # -- ClippetAdapter protocol: run / run_async ----------------------------

    def run(self, request: AgentRequest) -> AgentResult:
        """Execute via the Chat Completions API and wrap into ``AgentResult``.

        This method exists so ``OpenAIAdapter`` can be used interchangeably
        with subprocess-based adapters inside :class:`ClippetRunner`.
        """

        start_time = time.monotonic()

        try:
            completion = self.call(
                request,
                extra_skills=request.injected_skills or None,
            )
            raw_output = completion.model_dump_json(indent=2)

            # Extract the assistant message text
            text = ""
            if completion.choices:
                message = completion.choices[0].message
                if message and message.content:
                    text = message.content

            return AgentResult(
                raw_output=raw_output,
                is_success=True,
                execution_time=time.monotonic() - start_time,
                steps_count=1,
            )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"OpenAI API call failed: {e}",
            )

    async def run_async(self, request: AgentRequest) -> AgentResult:
        """Async version of :meth:`run`."""

        start_time = time.monotonic()

        try:
            completion = await self.call_async(
                request,
                extra_skills=request.injected_skills or None,
            )
            raw_output = completion.model_dump_json(indent=2)

            return AgentResult(
                raw_output=raw_output,
                is_success=True,
                execution_time=time.monotonic() - start_time,
                steps_count=1,
            )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"OpenAI API call failed: {e}",
            )

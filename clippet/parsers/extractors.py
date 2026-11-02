"""Output parsers for extracting structured data from CLI agent outputs."""

import json
from typing import Any

from clippet.models import ToolCallRecord


def parse_claude_json_output(raw_output: str) -> dict[str, Any]:
    """Parse Claude Code JSON output format.
    
    Claude Code with `--output-format json` returns a JSON object with fields:
    - type, subtype, cost_usd, duration_ms, duration_api_ms
    - is_error, num_turns, result (text output)
    - session_id, total_cost_usd
    
    Args:
        raw_output: Raw stdout from Claude Code CLI.
        
    Returns:
        Structured dict with keys:
        - result_text: The main text output
        - is_error: Whether the execution resulted in an error
        - cost_usd: Cost in USD for this call
        - num_turns: Number of conversation turns
        - duration_ms: Duration in milliseconds
        - session_id: The session identifier
    """
    result: dict[str, Any] = {
        "result_text": "",
        "is_error": False,
        "cost_usd": 0.0,
        "num_turns": 0,
        "duration_ms": 0,
        "session_id": None,
    }
    
    if not raw_output or not raw_output.strip():
        return result
    
    try:
        data = json.loads(raw_output.strip())
        
        if isinstance(data, dict):
            result["result_text"] = data.get("result", "")
            result["is_error"] = data.get("is_error", False)
            result["cost_usd"] = data.get("cost_usd", 0.0) or data.get("total_cost_usd", 0.0)
            result["num_turns"] = data.get("num_turns", 0)
            result["duration_ms"] = data.get("duration_ms", 0)
            result["session_id"] = data.get("session_id")
            
    except json.JSONDecodeError:
        # If not valid JSON, treat the entire output as result text
        result["result_text"] = raw_output.strip()
        result["is_error"] = True
        
    except Exception:
        # Catch any other exceptions to be defensive
        result["result_text"] = raw_output.strip()
        result["is_error"] = True
    
    return result


def parse_codex_output(raw_output: str) -> dict[str, Any]:
    """Parse Codex CLI output.
    
    Codex exec outputs the result text directly to stdout.
    Attempts to parse as JSON first; if not JSON, treats as plain text.
    
    Args:
        raw_output: Raw stdout from Codex CLI.
        
    Returns:
        Structured dict with keys:
        - result_text: The main text output
        - is_error: Whether the execution resulted in an error
    """
    result: dict[str, Any] = {
        "result_text": "",
        "is_error": False,
    }
    
    if not raw_output or not raw_output.strip():
        return result
    
    stripped = raw_output.strip()
    
    try:
        data = json.loads(stripped)
        
        if isinstance(data, dict):
            # If JSON with known structure, extract fields
            result["result_text"] = data.get("result", data.get("output", stripped))
            result["is_error"] = data.get("is_error", data.get("error", False))
        else:
            # Valid JSON but not a dict (could be list, string, etc.)
            result["result_text"] = stripped
            
    except json.JSONDecodeError:
        # Not JSON, treat as plain text output
        result["result_text"] = stripped
        
    except Exception:
        # Defensive catch-all
        result["result_text"] = stripped
    
    return result


def parse_qoder_output(raw_output: str) -> dict[str, Any]:
    """Parse Qoder CLI output.
    
    Qoder chat outputs to stdout.
    Attempts to parse as JSON first; if not JSON, treats as plain text.
    
    Args:
        raw_output: Raw stdout from Qoder CLI.
        
    Returns:
        Structured dict with keys:
        - result_text: The main text output
        - is_error: Whether the execution resulted in an error
    """
    result: dict[str, Any] = {
        "result_text": "",
        "is_error": False,
    }
    
    if not raw_output or not raw_output.strip():
        return result
    
    stripped = raw_output.strip()
    
    try:
        data = json.loads(stripped)
        
        if isinstance(data, dict):
            # If JSON with known structure, extract fields
            result["result_text"] = data.get("result", data.get("output", data.get("response", stripped)))
            result["is_error"] = data.get("is_error", data.get("error", False))
        else:
            # Valid JSON but not a dict
            result["result_text"] = stripped
            
    except json.JSONDecodeError:
        # Not JSON, treat as plain text output
        result["result_text"] = stripped
        
    except Exception:
        # Defensive catch-all
        result["result_text"] = stripped
    
    return result


def extract_tool_calls_from_claude_json(data: dict | list) -> list[ToolCallRecord]:
    """Extract tool call records from Claude JSON response.
    
    When Claude returns JSON format, tool usage may appear in the response
    as tool_use blocks in the content array.
    
    Args:
        data: Parsed JSON data from Claude (dict or list).
        
    Returns:
        List of ToolCallRecord objects extracted from the response.
    """
    tool_calls: list[ToolCallRecord] = []
    
    if not data:
        return tool_calls
    
    try:
        # Handle list input (array of content blocks)
        if isinstance(data, list):
            for item in data:
                tool_calls.extend(_extract_tool_use_from_item(item))
        elif isinstance(data, dict):
            # Look for content array in the response
            content = data.get("content", [])
            if isinstance(content, list):
                for item in content:
                    tool_calls.extend(_extract_tool_use_from_item(item))
            
            # Also check for tool_calls or tools array
            raw_tool_calls = data.get("tool_calls", data.get("tools", []))
            if isinstance(raw_tool_calls, list):
                for tc in raw_tool_calls:
                    tool_calls.extend(_extract_tool_use_from_item(tc))
                    
            # Check for nested messages
            messages = data.get("messages", [])
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict):
                        msg_content = msg.get("content", [])
                        if isinstance(msg_content, list):
                            for item in msg_content:
                                tool_calls.extend(_extract_tool_use_from_item(item))
                                
    except Exception:
        # Be defensive - return whatever we've extracted so far
        pass
    
    return tool_calls


def _extract_tool_use_from_item(item: Any) -> list[ToolCallRecord]:
    """Extract tool use from a single content item.
    
    Args:
        item: A content block that may contain tool use information.
        
    Returns:
        List of ToolCallRecord objects (0 or 1 items typically).
    """
    tool_calls: list[ToolCallRecord] = []
    
    if not isinstance(item, dict):
        return tool_calls
    
    try:
        # Claude format: {"type": "tool_use", "name": "...", "input": {...}}
        item_type = item.get("type", "")
        
        if item_type == "tool_use":
            tool_name = item.get("name", "")
            tool_input = item.get("input", {})
            
            if tool_name:
                tool_calls.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        parameters=tool_input if isinstance(tool_input, dict) else {},
                    )
                )
        
        # Alternative format: {"tool": "...", "arguments": {...}}
        elif "tool" in item:
            tool_name = item.get("tool", "")
            tool_args = item.get("arguments", item.get("args", item.get("input", {})))
            
            if tool_name:
                tool_calls.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        parameters=tool_args if isinstance(tool_args, dict) else {},
                    )
                )
                
        # Another format: {"function": {"name": "...", "arguments": {...}}}
        elif "function" in item:
            func = item.get("function", {})
            if isinstance(func, dict):
                tool_name = func.get("name", "")
                tool_args = func.get("arguments", {})
                
                # arguments might be a JSON string
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except json.JSONDecodeError:
                        tool_args = {}
                
                if tool_name:
                    tool_calls.append(
                        ToolCallRecord(
                            tool_name=tool_name,
                            parameters=tool_args if isinstance(tool_args, dict) else {},
                        )
                    )
                    
    except Exception:
        # Be defensive
        pass
    
    return tool_calls


def claude_usage(usage: Any, cost: Any = None) -> dict[str, Any] | None:
    """A Claude-protocol ``usage`` with the keys CLIppet shows added (cached tokens, cost)."""

    if not isinstance(usage, dict):
        return None
    out = dict(usage)
    cached = usage.get("cache_read_input_tokens")
    if isinstance(cached, (int, float)) and "cached_input_tokens" not in out:
        out["cached_input_tokens"] = int(cached)
    if isinstance(cost, (int, float)):
        out["cost_usd"] = float(cost)
    return out


def parse_claude_stream(raw_output: str) -> dict[str, Any]:
    """What a ``--output-format stream-json`` run said, one JSON event per line.

    Claude Code speaks this protocol (``system``/``init``, ``assistant``,
    ``user``, ``result``), and so does Qoder CLI.  The ``result`` event at the
    end carries the answer; a run that ends without one did not finish.

    Returns a dict with ``session_id``, ``answer``, ``is_error``, ``error``,
    ``tool_calls``, ``usage``, ``num_turns`` and the ``result`` event itself.
    """

    out: dict[str, Any] = {"session_id": None, "answer": None, "is_error": False, "error": None,
                           "tool_calls": [], "usage": None, "num_turns": 0, "result": None}
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("session_id"), str) and event["session_id"]:
            out["session_id"] = event["session_id"]
        kind = event.get("type")
        if kind == "assistant" and isinstance(event.get("message"), dict):
            for block in event["message"].get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    arguments = block.get("input")
                    out["tool_calls"].append(ToolCallRecord(tool_name=str(block.get("name") or "tool"),
                                                            parameters=arguments if isinstance(arguments, dict) else {}))
        elif kind == "result":
            out["result"] = event
    result = out["result"]
    if result is None:
        out["is_error"], out["error"] = True, "the run ended without a result"
        return out
    answer = result.get("result")
    out["answer"] = answer if isinstance(answer, str) else None
    out["num_turns"] = result.get("num_turns") or 0
    out["usage"] = claude_usage(result.get("usage"), result.get("total_cost_usd"))
    # an error result can say subtype "success" (a login failure, say): is_error decides
    if result.get("is_error") or str(result.get("subtype") or "").startswith("error"):
        out["is_error"] = True
        errors = result.get("errors")
        detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) and errors else None
        out["error"] = detail or out["answer"] or str(result.get("subtype") or "the agent reported an error")
    return out

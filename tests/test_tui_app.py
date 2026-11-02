"""The TUI app driven headless (needs Textual)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys

import pytest

from clippet.config.environments import list_environments
from clippet.tui.runs import Run

pytest.importorskip("textual")

from clippet.adapters.codex import CodexAdapter  # noqa: E402
from clippet.tui import app as tui_app  # noqa: E402
from clippet.tui.app import ClippetApp  # noqa: E402

FAKE_CODEX = """
import json, sys, time
def say(e, pause=0.0):
    print(json.dumps(e), flush=True); time.sleep(pause)
say({"type": "thread.started", "thread_id": "th-1"})
say({"type": "item.started", "item": {"id": "c", "type": "command_execution", "command": "ls", "status": "in_progress"}},
    float(sys.argv[1]))
say({"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "ls", "exit_code": 0,
                                        "aggregated_output": "", "status": "completed"}})
say({"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "pong"}})
say({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}})
"""


class FakeCodex(CodexAdapter):
    pause = "0.1"

    def build_command(self, request):
        return [sys.executable, "-c", FAKE_CODEX, self.pause]


def _app(world) -> ClippetApp:
    app = ClippetApp(world["work"], prefs_path=world["tmp"] / "tui.json")
    app.prefs.sources = [str(world["export"])]
    return app


async def _until(pilot, condition, timeout: float = 15.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not reached")


def test_app_opens_on_the_envs_and_moves_between_places(world) -> None:
    async def scenario() -> None:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.place == "envs"
            assert [e.name for e in app.envs] == ["base", "alice", "bob", "scratch"]
            assert app.selected_env == "alice"
            block = app.place_named("envs").codex_block(app.env_named("alice")).plain
            assert "quota" in block and "10% left" in block
            await pilot.press("2")
            assert app.place == "accounts"
            assert len(app.accounts) == 3
            await pilot.press("5")
            assert app.place == "checks"
            await pilot.press("question_mark")
            assert type(app.screen).__name__ == "Help"
            await pilot.press("escape")
            await pilot.press("T")
            assert app.theme == "clippet-paper"

    asyncio.run(scenario())
    assert json.loads((world["tmp"] / "tui.json").read_text())["theme"] == "clippet-paper"


def test_dragging_the_line_between_panes_resizes_them(world) -> None:
    from clippet.tui.widgets import Divider

    def parts(app):
        place = app.place_named("envs")
        return place.query_one(".list-col"), place.query_one(Divider)

    async def drag(pilot, line, by: int) -> None:
        x, y = line.region.x, line.region.y + 5  # screen positions: the line moves while it is dragged
        await pilot.mouse_down(offset=(x, y))
        await pilot.hover(offset=(x + by, y))
        await pilot.mouse_up(offset=(x + by, y))
        await pilot.pause()

    async def resize() -> int:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            column, line = parts(app)
            before = column.outer_size.width
            await drag(pilot, line, -20)
            assert abs(column.outer_size.width - (before - 20)) <= 1
            await drag(pilot, line, -line.region.x)  # never narrower than its least
            assert abs(column.outer_size.width - line.least) <= 1
            await drag(pilot, line, before - 20 - column.outer_size.width)
            assert abs(column.outer_size.width - (before - 20)) <= 1
            return before

    before = asyncio.run(resize())
    assert set(json.loads((world["tmp"] / "tui.json").read_text())["widths"]) == {"envs"}

    async def reopen() -> None:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            column, line = parts(app)
            assert abs(column.outer_size.width - (before - 20)) <= 1  # remembered
            await pilot.click(line, offset=(0, 5), times=2)  # a double click puts it back
            await pilot.pause()
            assert column.outer_size.width == before

    asyncio.run(reopen())
    assert json.loads((world["tmp"] / "tui.json").read_text())["widths"] == {}


def test_a_prompt_runs_in_every_marked_env(world, monkeypatch) -> None:
    monkeypatch.setattr(tui_app, "adapter_for", lambda env, agent, sandbox=None: FakeCodex(sandbox=sandbox))

    async def scenario() -> ClippetApp:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("space")  # alice
            await pilot.press("down")
            await pilot.press("space")  # bob
            assert app.targets() == ["alice", "bob"]
            await pilot.press("i")
            for ch in "ping":
                await pilot.press(ch)
            await pilot.press("enter")
            await _until(pilot, lambda: len(app.runs) == 2 and not any(r.active for r in app.runs))
            assert app.place == "runs"
        return app

    app = asyncio.run(scenario())
    assert sorted(r.env for r in app.runs) == ["alice", "bob"]
    assert all(r.state == "done" and r.answer == "pong" and r.session_id == "th-1" for r in app.runs)
    assert all(r.sandbox == "workspace-write" for r in app.runs)


def test_a_running_prompt_can_be_stopped(world, monkeypatch) -> None:
    slow = type("SlowCodex", (FakeCodex,), {"pause": "30"})
    monkeypatch.setattr(tui_app, "adapter_for", lambda env, agent, sandbox=None: slow())

    async def scenario() -> Run:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            run = app.start_runs("ping", ["alice"])[0]
            await _until(pilot, lambda: run.state == "running" and run.steps)
            app.cancel_run(run.id)
            await _until(pilot, lambda: run.state == "cancelled")
            return run

    run = asyncio.run(scenario())
    assert run.state == "cancelled" and run.ended is not None


def test_launching_hands_the_terminal_to_the_agent(world, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ClippetApp, "suspend", lambda self: contextlib.nullcontext())
    monkeypatch.setattr(tui_app, "launch_interactive",
                        lambda env, agent, args, cwd: calls.append((env.name, agent, args, cwd)) or (0, ["pulled"]))

    async def scenario() -> None:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.press("r")
            await pilot.pause()

    asyncio.run(scenario())
    assert calls == [("alice", "codex", [], world["work"]), ("alice", "codex", ["resume", "--last"], world["work"])]


def test_enter_shows_who_and_where_before_launching(world, monkeypatch) -> None:
    from textual.widgets import Input

    from clippet.tui.views.launch import Launch

    calls = []
    monkeypatch.setattr(ClippetApp, "suspend", lambda self: contextlib.nullcontext())
    monkeypatch.setattr(tui_app, "launch_interactive",
                        lambda env, agent, args, cwd: calls.append((env.name, agent, args, cwd)) or (0, []))

    async def scenario() -> None:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            assert isinstance(app.screen, Launch)
            facts = app.screen.summary()
            assert "alice@example.com" in facts and "newest login is pulled first" in facts  # who, and from where
            assert str(world["work"]) in facts and "clippet -e alice codex" in facts  # the folder and the command
            await pilot.press("enter")  # as shown: codex, no arguments
            await pilot.pause()
            assert not isinstance(app.screen, Launch)

            await pilot.press("enter")
            app.screen.query_one("#launch-args", Input).value = "resume --last"
            await pilot.pause()
            assert "clippet -e alice codex -- resume --last" in app.screen.summary()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.press("tab")  # another agent
            assert "claude's own login" in app.screen.summary()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.press("escape")  # back, nothing launched
            await pilot.pause()

    asyncio.run(scenario())
    work = world["work"]
    assert calls == [("alice", "codex", [], work), ("alice", "codex", ["resume", "--last"], work),
                     ("alice", "claude", [], work)]


def test_an_account_goes_into_a_new_env(world) -> None:
    async def scenario() -> ClippetApp:
        app = _app(world)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.press("down", "down")  # carol
            await pilot.press("enter")
            assert type(app.screen).__name__ == "Form"
            name = app.screen.query_one("#f-name")
            assert name.value == "carol"
            await pilot.press("ctrl+s")
            await pilot.pause()
        return app

    asyncio.run(scenario())
    envs = list_environments()
    assert "carol" in envs and envs["carol"]["codex_source"]["email"] == "carol@example.com"
    assert envs["carol"]["codex_source"]["linked"] is False  # a file export is not linked by default


def test_clippet_tui_is_a_subcommand() -> None:
    from clippet.cli import _build_parser

    args = _build_parser().parse_args(["tui", "/tmp"])
    assert args.command == "tui" and args.workspace == "/tmp"

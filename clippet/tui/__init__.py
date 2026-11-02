"""CLIppet's terminal UI (``clippet tui``): envs, accounts, runs, the project
config and checks in one place, in papertown's hand-drawn paper palette.

Needs the ``tui`` extra (``pip install 'clippet[tui]'``).
"""

from __future__ import annotations

from pathlib import Path


def textual_available() -> bool:
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def run_tui(workspace: Path | None = None) -> None:
    """Open the TUI; raises ImportError with an install hint without Textual."""

    if not textual_available():
        raise ImportError("The TUI needs Textual: pip install 'clippet[tui]'")
    from clippet.tui.app import run_tui as _run

    _run(workspace)

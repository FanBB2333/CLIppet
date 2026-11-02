"""The places of the TUI, in the order of the index on the left."""

from __future__ import annotations

from clippet.tui.views.accounts import AccountsPlace
from clippet.tui.views.checks import ChecksPlace
from clippet.tui.views.envs import EnvsPlace
from clippet.tui.views.project import ProjectPlace
from clippet.tui.views.runs import RunsPlace


def make_places() -> list:
    return [EnvsPlace(), AccountsPlace(), RunsPlace(), ProjectPlace(), ChecksPlace()]


__all__ = ["AccountsPlace", "ChecksPlace", "EnvsPlace", "ProjectPlace", "RunsPlace", "make_places"]

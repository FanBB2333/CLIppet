"""The user's own session, as it was before a CLIppet run changed it.

A run in an env may give its agent another HOME (isolation scope ``home``)
and drop the XDG base-directory variables.  Everything the agent starts
inherits that, a nested ``clippet`` included.  So every run CLIppet starts
also carries the user's own values (``CLIPPET_HOST_HOME``,
``CLIPPET_HOST_XDG_CONFIG_HOME``, ...), and CLIppet reads them back when it
runs inside one: its registry, the real HOME's dotfiles and the next run's
environment then come from the user's session, not from the env of the run it
is nested in.  ``os.environ`` is never changed.

This module imports nothing from the rest of CLIppet, so anything may import it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: The variables that name the user's own directories.
HOST_DIR_VARS: tuple[str, ...] = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)

#: Prefix of the variables that carry them into the runs CLIppet starts.
HOST_PREFIX = "CLIPPET_HOST_"

#: Set in every run CLIppet starts: inside one, the user's own HOME.
HOST_HOME_VAR = HOST_PREFIX + "HOME"


@dataclass(frozen=True)
class Host:
    """The user's own HOME and XDG directories (the unset ones are absent)."""

    home: Path
    dirs: dict[str, str] = field(default_factory=dict)  # HOST_DIR_VARS that are set, HOME included
    nested: bool = False  # read back inside a run CLIppet started

    def context(self) -> dict[str, str]:
        """The variables that hand this host on to a run."""

        return {HOST_PREFIX + name: value for name, value in self.dirs.items()}


def host(environ: Mapping[str, str] | None = None) -> Host:
    """The user's own session, seen from *environ* (default: this process)."""

    source = os.environ if environ is None else environ
    recorded = source.get(HOST_HOME_VAR)
    if recorded:
        dirs = {name: source[HOST_PREFIX + name] for name in HOST_DIR_VARS if source.get(HOST_PREFIX + name)}
        dirs["HOME"] = recorded
        return Host(home=Path(recorded), dirs=dirs, nested=True)
    home = Path.home() if environ is None or not source.get("HOME") else Path(source["HOME"])
    dirs = {name: source[name] for name in HOST_DIR_VARS if source.get(name)}
    dirs["HOME"] = str(home)
    return Host(home=home, dirs=dirs)


def host_home(environ: Mapping[str, str] | None = None) -> Path:
    """The user's own HOME, also when CLIppet runs inside a run with another one."""

    return host(environ).home

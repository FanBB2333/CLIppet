"""Isolated runtime environments for adapter subprocesses."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Mapping, Protocol


# ---------------------------------------------------------------------------
# Well-known config paths per agent (relative to HOME)
# ---------------------------------------------------------------------------

AGENT_CONFIG_PATHS: dict[str, list[str]] = {
    "claude": [
        ".claude",          # ~/.claude/ directory (settings.json, skills/, etc.)
        ".claude.json",     # MCP config at HOME root
    ],
    "codex": [
        ".codex",           # ~/.codex/ directory (auth.json, config.toml, etc.)
    ],
    "gemini": [
        ".gemini",          # ~/.gemini/ directory (.env, settings.json, etc.)
    ],
    "qodercli": [
        ".qoder",           # ~/.qoder/ directory (sessions, projects, skills, etc.)
        ".qoder-cli",       # ~/.qoder-cli/ai-stats (commit AI-contribution stats)
    ],
    "opencode": [
        ".config/opencode", # ~/.config/opencode/ (opencode.json, .env, etc.)
    ],
}


# ---------------------------------------------------------------------------
# Environment variables that must not leak into an isolated run
# ---------------------------------------------------------------------------

# Variables that point a tool at a config/data directory *outside* $HOME.  When
# HOME is replaced they would silently redirect agents back to the real home.
HOME_POINTER_ENV_VARS: tuple[str, ...] = (
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)

# Per-agent variables that select a config directory, credential or endpoint.
# Inheriting them would let the parent shell's identity override the identity
# carried by the isolated HOME (e.g. CODEX_HOME pointing at ~/.codex).
AGENT_IDENTITY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "codex": (
        "CODEX_HOME",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_ORGANIZATION",
        "OPENAI_PROJECT",
        "OPENAI_PROJECT_ID",
    ),
    "claude": (
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_*",
    ),
    "gemini": (
        "GEMINI_CLI_HOME",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GOOGLE_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ),
    "qodercli": (),
}


def scrub_patterns_for(agent: str | None) -> tuple[str, ...]:
    """Return the env-var names/prefix patterns to drop for an isolated *agent* run."""

    patterns = list(HOME_POINTER_ENV_VARS)
    if agent:
        patterns.extend(AGENT_IDENTITY_ENV_VARS.get(agent, ()))
    return tuple(patterns)


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True
    return False


def _write_private_file(path: Path, data: bytes) -> None:
    """Atomically write *data* to *path* with ``0600`` permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class IsolatedEnvironment:
    """Create a sandboxed HOME directory and environment for one agent run.

    Supports two modes:

    * **Temp-dir mode** (default): creates a fresh ``clippet-agent-*`` directory
      under ``base_dir`` (or the system temp root).  The directory is removed on
      ``cleanup()`` unless ``persist`` is *True*.
    * **Second-home mode**: when ``home_dir`` is given, the supplied path is used
      *directly* as ``$HOME``.  No temp directory is created and the path is
      **never** removed by CLIppet.

    ``scrub_env`` lists inherited variables to drop (exact names, or prefixes
    ending in ``*``).  It is applied after the whitelist/blacklist and before
    ``env_overrides``, so explicit overrides always win.
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        home_dir: Path | None = None,
        persist: bool = False,
        inherit_env: bool = True,
        env_overrides: dict[str, str] | None = None,
        env_whitelist: list[str] | None = None,
        env_blacklist: list[str] | None = None,
        scrub_env: Iterable[str] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser() if base_dir else None
        self._home_dir_override = Path(home_dir).expanduser() if home_dir else None
        self._persist = persist
        self._inherit_env = inherit_env
        self._env_overrides = dict(env_overrides or {})
        self._env_whitelist = list(env_whitelist) if env_whitelist is not None else None
        self._env_blacklist = list(env_blacklist) if env_blacklist is not None else None
        self._scrub_env = tuple(scrub_env or ())
        self._home_dir: Path | None = None
        self._env: dict[str, str] | None = None
        self._is_second_home: bool = False

    @property
    def home_dir(self) -> Path:
        """Return the sandbox HOME directory."""

        if self._home_dir is None:
            raise RuntimeError("IsolatedEnvironment has not been entered")
        return self._home_dir

    @property
    def env(self) -> dict[str, str]:
        """Return the subprocess environment for the sandbox."""

        if self._env is None:
            raise RuntimeError("IsolatedEnvironment has not been entered")
        return self._env

    @property
    def is_second_home(self) -> bool:
        """Return True when running in second-home (persistent directory) mode."""
        return self._is_second_home

    def __enter__(self) -> IsolatedEnvironment:
        """Create the sandbox directory and environment."""

        self._home_dir = self._create_home_dir()
        self._env = self._build_env()
        return self

    def __exit__(self, *exc: object) -> None:
        """Tear down the sandbox unless persistence is enabled."""

        self.cleanup()

    def cleanup(self) -> None:
        """Delete the sandbox directory when persistence is disabled.

        Second-home directories are **never** deleted.
        """

        if self._home_dir is None:
            return

        cleanup_target = self._home_dir
        self._home_dir = None
        self._env = None

        # Never delete a second-home directory
        if self._is_second_home:
            return

        if not self._persist:
            shutil.rmtree(cleanup_target, ignore_errors=True)

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Resolve a sandbox-relative path under the virtual HOME directory."""

        sandbox_relative_path = Path(relative_path)
        if sandbox_relative_path.is_absolute():
            raise ValueError("Sandbox paths must be relative to the isolated HOME")

        destination = (self.home_dir / sandbox_relative_path).resolve()
        sandbox_root = self.home_dir.resolve()

        if destination != sandbox_root and sandbox_root not in destination.parents:
            raise ValueError("Sandbox path escapes the isolated HOME directory")

        return destination

    def update_env(self, variables: dict[str, str]) -> None:
        """Apply environment variable overrides to the sandbox."""

        self._env_overrides.update(variables)
        if self._env is not None:
            self._env.update(variables)
            self._env["HOME"] = str(self.home_dir)

    def _create_home_dir(self) -> Path:
        # Second-home mode: use the pre-existing directory directly
        if self._home_dir_override is not None:
            self._home_dir_override.mkdir(parents=True, exist_ok=True)
            self._is_second_home = True
            return self._home_dir_override

        # Temp-dir mode
        parent_dir: str | None = None

        if self._base_dir is not None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            parent_dir = str(self._base_dir)

        return Path(tempfile.mkdtemp(prefix="clippet-agent-", dir=parent_dir))

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ) if self._inherit_env else {}

        if self._env_whitelist is not None:
            allowed = set(self._env_whitelist)
            env = {key: value for key, value in env.items() if key in allowed}

        if self._env_blacklist is not None:
            for key in self._env_blacklist:
                env.pop(key, None)

        if self._scrub_env:
            env = {
                key: value
                for key, value in env.items()
                if not _matches_any(key, self._scrub_env)
            }

        env.update(self._env_overrides)
        env["HOME"] = str(self.home_dir)

        return env


class CredentialProvider(Protocol):
    """Protocol for injecting credentials into an isolated environment."""

    def inject(self, env: IsolatedEnvironment) -> None:
        """Populate credentials inside the sandbox."""


class FileCredentialProvider:
    """Copy credential files into the sandbox HOME directory."""

    def __init__(self, mappings: dict[str, Path | str]) -> None:
        self._mappings = {
            relative_path: Path(source_path).expanduser()
            for relative_path, source_path in mappings.items()
        }

    def inject(self, env: IsolatedEnvironment) -> None:
        """Copy mapped credential files into the sandbox."""

        for relative_path, source_path in self._mappings.items():
            source = source_path.resolve()
            if not source.is_file():
                raise FileNotFoundError(f"Credential source file not found: {source}")

            destination = env.resolve_path(relative_path)
            _write_private_file(destination, source.read_bytes())


class ContentCredentialProvider:
    """Write generated credential files (e.g. a converted ``auth.json``)."""

    def __init__(self, contents: Mapping[str, object]) -> None:
        self._contents = {
            relative_path: _secret_value(value)
            for relative_path, value in contents.items()
        }

    def inject(self, env: IsolatedEnvironment) -> None:
        """Write each generated file into the sandbox with ``0600`` permissions."""

        for relative_path, content in self._contents.items():
            destination = env.resolve_path(relative_path)
            _write_private_file(destination, content.encode("utf-8"))


def _secret_value(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value)


class DirectoryCopyProvider:
    """Copy an entire directory tree into the sandbox HOME.

    Useful for copying a real agent config directory (e.g. ``~/.codex/``) into
    a temp sandbox so that *all* config files are available, not just the auth
    file.  Sockets, FIFOs and device files are skipped.
    """

    def __init__(self, mappings: dict[str, Path | str]) -> None:
        """Initialise with ``{relative_dest: source_dir}`` mappings."""
        self._mappings = {
            relative_dest: Path(source_dir).expanduser()
            for relative_dest, source_dir in mappings.items()
        }

    def inject(self, env: IsolatedEnvironment) -> None:
        """Copy directory trees into the sandbox."""

        for relative_dest, source_dir in self._mappings.items():
            source = source_dir.resolve()
            if not source.is_dir():
                continue  # silently skip missing directories

            destination = env.resolve_path(relative_dest)
            if destination.exists():
                shutil.rmtree(destination)
            copy_tree(source, destination)


class EnvVarCredentialProvider:
    """Inject credentials via environment variables."""

    def __init__(self, variables: dict[str, str]) -> None:
        self._variables = dict(variables)

    def inject(self, env: IsolatedEnvironment) -> None:
        """Apply variables to the sandbox environment."""

        env.update_env(self._variables)


class CredentialSet:
    """Apply multiple credential providers in sequence."""

    def __init__(self, providers: list[CredentialProvider]) -> None:
        self._providers = list(providers)

    def inject(self, env: IsolatedEnvironment) -> None:
        """Inject all configured credential sources into the sandbox."""

        for provider in self._providers:
            provider.inject(env)


class CredentialSyncBack:
    """Copy credential files the agent rewrote during a run back to their owner.

    OAuth-based CLIs (Codex, Claude Code, ...) rotate refresh tokens and
    rewrite their credential file in place.  In temp-HOME mode that file is
    deleted on cleanup, which would discard the only valid refresh token.  A
    ``CredentialSyncBack`` snapshots the injected files after injection and,
    after the run, writes every file whose content changed to its destination.
    """

    def __init__(self, mappings: Mapping[str, str]) -> None:
        self._mappings = {
            relative: Path(destination).expanduser()
            for relative, destination in mappings.items()
        }
        self._snapshots: dict[str, bytes | None] = {}

    def snapshot(self, env: IsolatedEnvironment) -> None:
        """Record the current content of every watched sandbox file."""

        for relative in self._mappings:
            path = env.resolve_path(relative)
            self._snapshots[relative] = path.read_bytes() if path.is_file() else None

    def finalize(self, env: IsolatedEnvironment) -> list[Path]:
        """Write back changed files; return the destinations that were updated."""

        updated: list[Path] = []
        for relative, destination in self._mappings.items():
            path = env.resolve_path(relative)
            if not path.is_file():
                continue
            current = path.read_bytes()
            if current == self._snapshots.get(relative):
                continue
            _write_private_file(destination, current)
            updated.append(destination)
        return updated


# ---------------------------------------------------------------------------
# Directory copy helpers
# ---------------------------------------------------------------------------


def _is_special_file(path: Path) -> bool:
    import stat

    try:
        mode = path.lstat().st_mode
    except OSError:
        return True
    return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode))


def copy_tree(
    source: Path,
    destination: Path,
    *,
    exclude: Iterable[str] = (),
) -> None:
    """Copy *source* to *destination* preserving symlinks.

    Sockets, FIFOs and device files (e.g. a running agent's IPC socket) are
    skipped instead of aborting the whole copy.  ``exclude`` holds ``fnmatch``
    patterns matched against entry names at every level.
    """

    import fnmatch

    patterns = tuple(exclude)

    def _ignore(directory: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                skipped.add(name)
            elif _is_special_file(Path(directory) / name):
                skipped.add(name)
        return skipped

    shutil.copytree(source, destination, symlinks=True, ignore=_ignore)


def ensure_shared_links(
    home_dir: Path,
    entries: Iterable[str],
    real_home: Path | None = None,
) -> list[str]:
    """Symlink selected dotfiles from the real HOME into an isolated HOME.

    Only *missing* destinations are created; existing files, directories or
    links inside the isolated HOME are never touched.  Entries whose source
    does not exist are skipped.  Returns the entries that were linked.
    """

    real_home = (real_home or Path.home()).expanduser()
    home_dir = Path(home_dir)
    if home_dir.resolve() == real_home.resolve():
        return []

    linked: list[str] = []
    for entry in entries:
        relative = Path(entry)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        source = real_home / relative
        destination = home_dir / relative
        if not source.exists() or destination.exists() or destination.is_symlink():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source, target_is_directory=source.is_dir())
        linked.append(entry)
    return linked

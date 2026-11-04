"""Named environment profile management for CLIppet.

Two entry types are supported:

* ``home`` (default for new entries) — a persistent fake ``$HOME`` living under
  ``~/.clippet/envs/<name>/``.  CLIppet creates and owns the directory, and
  all agent state (sessions, history, MCP, tokens) persists there across runs.
  At launch time an agent that can be pointed at its own config directory
  keeps your real ``$HOME`` and gets that directory inside the env (Codex:
  ``CODEX_HOME=<env>/.codex``, isolation scope ``agent``); the others get the
  env as ``$HOME`` (second-home mode, isolation scope ``home``).

* ``file`` (legacy) — a path alias pointing to an external config file.  The
  file is copied into a fresh temp HOME on each run via ``FileCredentialProvider``;
  agent state written during the run is discarded on exit.

The on-disk schema in ``~/.clippet/environments.json`` is::

    {
      "<name>": {
        "type": "home",              # or "file" (legacy entries may omit this)
        "home_dir": "/abs/path",     # present when type == "home"
        "config_path": "/abs/path",  # present when type == "file"
        "description": "",
        "share": [".gitconfig"],     # optional: dotfiles linked from the real HOME
        "codex_source": {...},       # optional: where the Codex login came from
        "isolation_scope": "home",   # optional: "agent" or "home"; absent: the default
        "vars": {"codex": {...}}     # optional: variables set for an agent's runs
      },
      ...
    }

An env is an *identity container*: seeding from the current HOME copies
configuration only.  Credentials (OAuth refresh tokens in particular) are
copied only on request, because OpenAI and Anthropic rotate refresh tokens —
two copies refreshing independently invalidate each other.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clippet.agents import AGENTS, SeedSpec
from clippet.host import host_home
from clippet.isolation import ISOLATION_SCOPES, copy_tree

ENV_TYPE_HOME = "home"
ENV_TYPE_FILE = "file"

# "base" is how pickers refer to the real $HOME.
RESERVED_ENV_NAMES = frozenset({"base"})

_ENV_NAME_RE = re.compile(r"^[^\W][\w.\-]*$")

# Dotfiles linked (not copied) from the real HOME into new envs so that tools
# the agent runs (git commit, ...) keep the user's identity and settings.
DEFAULT_SHARED_ENTRIES: tuple[str, ...] = (".gitconfig", ".config/git")


# What --from-current copies for each agent lives with the rest of what CLIppet
# knows about that agent (clippet.agents); these names are kept for callers.
SEED_SPECS: dict[str, SeedSpec] = {key: spec.seed for key, spec in AGENTS.items() if spec.seed is not None}

SEEDABLE_AGENTS: tuple[str, ...] = tuple(SEED_SPECS)

# Identity fields Claude Code keeps in ~/.claude.json next to MCP settings.
_CLAUDE_JSON_IDENTITY_KEYS = AGENTS["claude"].seed.scrub[".claude.json"]


class EnvironmentRegistryError(ValueError):
    """Raised when ``environments.json`` cannot be read safely."""


@dataclass
class SeedReport:
    """What a seeding/cloning step copied and what it deliberately skipped."""

    copied: list[str] = field(default_factory=list)
    skipped_credentials: list[str] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)


#: Environment variable that relocates CLIppet's state directory.
CLIPPET_HOME_ENV = "CLIPPET_HOME"


def get_clippet_root() -> Path:
    """Return CLIppet's per-user state directory.

    ``$CLIPPET_HOME`` when set (handy for tests, CI or separate registries),
    otherwise ``~/.clippet/`` in your own HOME, also when CLIppet runs inside
    an agent whose HOME is an env.
    """

    override = os.environ.get(CLIPPET_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return host_home() / ".clippet"


def get_environments_file() -> Path:
    """Return the path to the environments JSON file."""

    return get_clippet_root() / "environments.json"


def get_envs_root() -> Path:
    """Return ``~/.clippet/envs/`` — root directory for HOME containers."""

    return get_clippet_root() / "envs"


def validate_env_name(name: str) -> str:
    """Return *name* if it is usable as an env name, else raise ``ValueError``."""

    if (
        not name
        or name in (".", "..")
        or name.startswith(".")
        or not _ENV_NAME_RE.match(name)
    ):
        raise ValueError(
            f"Invalid environment name: '{name}'. Use letters, digits, '_', '-' "
            "or '.', not starting with '.' (e.g. 'work', 'acct-2')."
        )
    if name in RESERVED_ENV_NAMES:
        raise ValueError(f"'{name}' is reserved (it means the real $HOME).")
    return name


def env_home_path(name: str) -> Path:
    """Return the canonical HOME path for an env name (does not check existence)."""

    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("."):
        raise ValueError(
            f"Invalid environment name: '{name}'. "
            "Names must be non-empty, must not contain '/', and must not start with '.'."
        )
    return get_envs_root() / name


def load_environments() -> dict[str, dict]:
    """Read and parse the environments JSON file.

    Raises:
        EnvironmentRegistryError: If the file exists but is not a JSON object.
            (Silently treating it as empty would make the next save wipe every
            registered env.)
    """

    env_file = get_environments_file()

    if not env_file.exists():
        return {}

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise EnvironmentRegistryError(
            f"Environment registry '{env_file}' is not valid JSON ({exc}). "
            "Fix or move the file aside; CLIppet will not overwrite it."
        ) from exc

    if not isinstance(data, dict):
        raise EnvironmentRegistryError(
            f"Environment registry '{env_file}' must contain a JSON object."
        )
    return data


def save_environments(envs: dict[str, dict]) -> None:
    """Atomically write the environments dictionary as formatted JSON."""

    env_file = get_environments_file()
    env_file.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=".environments.", dir=str(env_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(envs, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, env_file)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def entry_type(profile: dict) -> str:
    """Return the type of an env entry. Legacy entries (no ``type`` field) are
    inferred from which path field is set."""

    declared = profile.get("type")
    if declared in (ENV_TYPE_HOME, ENV_TYPE_FILE):
        return declared
    if profile.get("home_dir"):
        return ENV_TYPE_HOME
    return ENV_TYPE_FILE


def shared_entries(profile: dict) -> tuple[str, ...]:
    """Dotfiles a HOME env links from the real HOME (default for legacy entries)."""

    share = profile.get("share")
    if isinstance(share, list):
        return tuple(str(item) for item in share)
    return DEFAULT_SHARED_ENTRIES


def get_environment(name: str) -> dict:
    """Return the environment profile with the given name."""

    envs = load_environments()

    if name not in envs:
        available = ", ".join(sorted(envs.keys())) if envs else "(none)"
        raise KeyError(
            f"Environment '{name}' not found. "
            f"Available environments: {available}"
        )

    return envs[name]


def update_environment(name: str, **fields: Any) -> dict:
    """Merge *fields* into an existing registry entry and save it."""

    envs = load_environments()
    if name not in envs:
        raise KeyError(f"Environment '{name}' not found.")
    envs[name].update(fields)
    save_environments(envs)
    return envs[name]


def env_vars(profile: dict, agent: str) -> dict[str, str]:
    """The variables an env sets for *agent*'s runs, as stored (``${NAME}`` unresolved)."""

    stored = profile.get("vars")
    values = stored.get(agent) if isinstance(stored, dict) else None
    if not isinstance(values, dict):
        return {}
    return {str(key): str(value) for key, value in values.items()}


def set_env_vars(
    name: str,
    agent: str,
    assign: dict[str, str] | None = None,
    unset: list[str] | tuple[str, ...] = (),
) -> dict[str, str]:
    """Set (*assign*) and remove (*unset*) variables env *name* sets for *agent*; returns them all."""

    envs = load_environments()
    if name not in envs:
        raise KeyError(f"Environment '{name}' not found.")
    profile = envs[name]
    stored = profile.get("vars") if isinstance(profile.get("vars"), dict) else {}
    values = env_vars(profile, agent)
    for key in unset:
        values.pop(key, None)
    values.update(assign or {})
    stored = {key: value for key, value in stored.items() if key != agent}
    if values:
        stored[agent] = dict(sorted(values.items()))
    if stored:
        profile["vars"] = stored
    else:
        profile.pop("vars", None)
    save_environments(envs)
    return values


#: Where an env keeps its isolation scope in environments.json.
ISOLATION_SCOPE_KEY = "isolation_scope"


def env_isolation_scope(profile: dict) -> str | None:
    """The isolation scope an env asks for (``agent`` / ``home``), or None for CLIppet's default."""

    value = profile.get(ISOLATION_SCOPE_KEY)
    return value if value in ISOLATION_SCOPES else None


def set_env_isolation_scope(name: str, scope: str | None) -> dict:
    """Make HOME env *name* run its agents in *scope* (None: back to the default); returns its profile.

    Nothing is moved: the agents' config, logins and sessions stay where they
    are in the env, whatever the scope.
    """

    if scope is not None and scope not in ISOLATION_SCOPES:
        raise ValueError(f"unknown isolation scope '{scope}': use 'agent', 'home' or 'default'")
    envs = load_environments()
    if name not in envs:
        raise KeyError(f"Environment '{name}' not found.")
    profile = envs[name]
    if entry_type(profile) != ENV_TYPE_HOME:
        raise ValueError(f"env '{name}' is a config-file alias; only HOME envs have an isolation scope")
    if scope is None:
        profile.pop(ISOLATION_SCOPE_KEY, None)
    else:
        profile[ISOLATION_SCOPE_KEY] = scope
    save_environments(envs)
    return profile


def isolation_scope_for_home(home_dir: Path | str) -> str | None:
    """The isolation scope the registered env living in *home_dir* asks for (None: the default)."""

    found = profile_for_home(home_dir)
    return env_isolation_scope(found[1]) if found is not None else None


def profile_for_home(home_dir: Path | str) -> tuple[str, dict] | None:
    """The registered HOME env whose directory is *home_dir*, as ``(name, profile)``."""

    target = Path(home_dir).expanduser().resolve()
    try:
        envs = load_environments()
    except EnvironmentRegistryError:
        return None
    for name, profile in envs.items():
        home = profile.get("home_dir")
        if entry_type(profile) == ENV_TYPE_HOME and home and Path(home).expanduser().resolve() == target:
            return name, profile
    return None


def add_environment(
    name: str,
    config_path: str | Path,
    description: str = "",
) -> None:
    """Register a **file-mode** env entry (legacy path alias).

    Use :func:`create_home_env` for the modern HOME-container mode.
    """

    config_path = Path(config_path).resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")

    envs = load_environments()
    envs[name] = {
        "type": ENV_TYPE_FILE,
        "config_path": str(config_path),
        "description": description,
    }
    save_environments(envs)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _copy_entry(src: Path, dst: Path, exclude: tuple[str, ...]) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir() and not src.is_symlink():
        copy_tree(src, dst, exclude=exclude)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def _copy_sanitized_claude_json(src: Path, dst: Path) -> None:
    """Copy ~/.claude.json (MCP servers, project settings) minus identity keys."""

    _copy_scrubbed_json(src, dst, _CLAUDE_JSON_IDENTITY_KEYS)


def _scrub_key(data: Any, path: str, prefix: str = "") -> list[str]:
    """Drop the key at dotted *path* from *data*; a last part ending in ``*`` drops every key it begins.

    Returns the dotted paths of the keys it dropped.
    """

    head, _, rest = path.partition(".")
    if not isinstance(data, dict):
        return []
    if rest:
        return _scrub_key(data.get(head), rest, f"{prefix}{head}.")
    if head.endswith("*"):
        dropped = [key for key in data if isinstance(key, str) and key.startswith(head[:-1])]
    else:
        dropped = [head] if head in data else []
    for key in dropped:
        data.pop(key)
    return [prefix + key for key in dropped]


def _copy_scrubbed_json(src: Path, dst: Path, keys: tuple[str, ...]) -> list[str] | None:
    """Copy a JSON settings file minus the *keys* (dotted paths) that carry a login.

    A file holding none of them is copied as it is.  Returns the keys left
    out, or *None* when the file is not JSON and was not copied (it may hold
    a login CLIppet cannot take out).
    """

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    dropped = [path for key in keys for path in _scrub_key(data, key)]
    if not dropped:
        _copy_entry(src, dst, ())
        return []
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    dst.chmod(0o600)
    return dropped


def validate_agents(agents: list[str] | None) -> list[str]:
    """Validate an ``--agents`` list (default: every seedable agent)."""

    if not agents:
        return list(SEEDABLE_AGENTS)
    unknown = [agent for agent in agents if agent not in SEED_SPECS]
    if unknown:
        raise ValueError(
            f"Unknown agent(s) for seeding: {', '.join(unknown)}. "
            f"Choose from: {', '.join(SEEDABLE_AGENTS)}."
        )
    return list(agents)


def seed_from_home(
    target: Path,
    agents: list[str],
    *,
    with_auth: bool = False,
    source_home: Path | None = None,
) -> SeedReport:
    """Copy agent configuration from *source_home* (default: real HOME) into *target*."""

    real_home = source_home or host_home()
    report = SeedReport()

    for agent in agents:
        spec = SEED_SPECS[agent]
        for rel in spec.entries:
            src = real_home / rel
            if not (src.exists() or src.is_symlink()):
                continue
            dst = target / rel
            if rel in spec.scrub and not with_auth:
                dropped = _copy_scrubbed_json(src, dst, spec.scrub[rel])
                if dropped is None:
                    report.skipped_credentials.append(f"{rel} (not JSON)")
                    continue
                report.skipped_credentials.extend(f"{rel}: {key}" for key in dropped)
            else:
                _copy_entry(src, dst, spec.exclude)
            report.copied.append(rel)
        for rel in spec.credentials:
            src = real_home / rel
            if not (src.is_file() or src.is_dir()):
                continue
            if with_auth:
                _copy_entry(src, target / rel, ())
                (target / rel).chmod(0o700 if (target / rel).is_dir() else 0o600)
                report.copied.append(rel)
            else:
                report.skipped_credentials.append(rel)
    return report


def _seed_from_real_home(target: Path, agents: list[str]) -> list[str]:
    """Backward-compatible helper: seed config and return seeded agent names."""

    seeded: list[str] = []
    for agent in validate_agents(agents):
        if seed_from_home(target, [agent]).copied:
            seeded.append(agent)
    return seeded


def _new_home_entry(home_dir: Path, description: str, share: list[str] | None) -> dict:
    return {
        "type": ENV_TYPE_HOME,
        "home_dir": str(home_dir.resolve()),
        "description": description,
        "share": list(DEFAULT_SHARED_ENTRIES if share is None else share),
    }


def create_home_env_with_report(
    name: str,
    *,
    from_current: bool = False,
    agents: list[str] | None = None,
    with_auth: bool = False,
    description: str = "",
    overwrite: bool = False,
    share: list[str] | None = None,
) -> tuple[Path, SeedReport]:
    """Create a HOME-container env and report what seeding copied/skipped."""

    from clippet.isolation import ensure_shared_links

    validate_env_name(name)
    home_dir = env_home_path(name)
    targets = validate_agents(agents) if from_current else []
    envs = load_environments()

    if name in envs and not overwrite:
        raise FileExistsError(
            f"Environment '{name}' already exists. "
            "Pass overwrite=True or remove it first."
        )
    if home_dir.exists() and not overwrite:
        raise FileExistsError(
            f"HOME directory already exists at '{home_dir}'. "
            "Pass overwrite=True or remove it first."
        )

    if home_dir.exists() and overwrite:
        shutil.rmtree(home_dir)

    home_dir.mkdir(parents=True, exist_ok=False)
    home_dir.chmod(0o700)  # holds credentials and session history
    report = SeedReport()
    try:
        if targets:
            report = seed_from_home(home_dir, targets, with_auth=with_auth)
        entry = _new_home_entry(home_dir, description, share)
        report.linked = ensure_shared_links(home_dir, entry["share"])
    except BaseException:
        shutil.rmtree(home_dir, ignore_errors=True)
        raise

    envs[name] = entry
    save_environments(envs)
    return home_dir, report


def create_home_env(
    name: str,
    *,
    from_current: bool = False,
    agents: list[str] | None = None,
    with_auth: bool = False,
    description: str = "",
    overwrite: bool = False,
    share: list[str] | None = None,
) -> Path:
    """Create a HOME-container env at ``~/.clippet/envs/<name>/``.

    Args:
        name: Env name. Must be a valid directory name (no ``/``, no leading dot).
        from_current: When True, copy agent **configuration** from the current
            HOME (settings, config.toml, MCP config, skills, prompts, ...).
            Runtime state (sessions, logs, caches, sockets) is never copied.
        agents: Restrict seeding to a subset of agents (``"claude"``, ``"codex"``,
            ``"qodercli"``, ...). Ignored when ``from_current`` is False.
            Default seeds all.
        with_auth: Also copy credential files (``.codex/auth.json``, ...).  The
            copy shares OAuth refresh tokens with the source HOME; prefer
            logging in inside the env for long-term parallel use.
        description: Optional human-readable description.
        overwrite: When True, replaces an existing env (registry + directory).
            When False (default), raises if the env already exists.
        share: Dotfiles to link from the real HOME (default
            :data:`DEFAULT_SHARED_ENTRIES`; pass ``[]`` to link nothing).

    Returns:
        The absolute path to the new HOME directory.
    """

    home_dir, _ = create_home_env_with_report(
        name,
        from_current=from_current,
        agents=agents,
        with_auth=with_auth,
        description=description,
        overwrite=overwrite,
        share=share,
    )
    return home_dir


def _strip_logins(home: Path) -> None:
    """Take every agent's login out of a copied HOME: its credential files and
    folders, and the login keys of its JSON settings."""

    for spec in SEED_SPECS.values():
        for rel in spec.credentials:
            path = home / rel
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        for rel, keys in spec.scrub.items():
            path = home / rel
            if not path.is_file() or path.is_symlink():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if [dropped for key in keys for dropped in _scrub_key(data, key)]:
                path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                path.chmod(0o600)


def clone_home_env(
    src: str,
    dst: str,
    *,
    description: str = "",
    overwrite: bool = False,
    with_auth: bool = False,
) -> Path:
    """Clone an existing HOME-container env to a new name.

    Everything is copied (sessions included) except credential files, unless
    ``with_auth`` is True.  Sockets and other special files are skipped.
    Only ``home``-type envs can be cloned.
    """

    profile = get_environment(src)
    if entry_type(profile) != ENV_TYPE_HOME:
        raise ValueError(
            f"Environment '{src}' is a file-mode entry and cannot be cloned. "
            "Use 'env add' to register the same config under a new name instead."
        )

    src_home = Path(profile["home_dir"])
    if not src_home.is_dir():
        raise FileNotFoundError(
            f"Source HOME directory '{src_home}' does not exist on disk."
        )

    validate_env_name(dst)
    dst_home = env_home_path(dst)
    envs = load_environments()

    if dst in envs and not overwrite:
        raise FileExistsError(f"Environment '{dst}' already exists.")
    if dst_home.exists() and not overwrite:
        raise FileExistsError(f"Target HOME directory already exists at '{dst_home}'.")

    if dst_home.exists() and overwrite:
        shutil.rmtree(dst_home)

    dst_home.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy_tree(src_home, dst_home)
        dst_home.chmod(0o700)
        if not with_auth:
            _strip_logins(dst_home)
    except BaseException:
        shutil.rmtree(dst_home, ignore_errors=True)
        raise

    entry = _new_home_entry(dst_home, description, list(shared_entries(profile)))
    if env_isolation_scope(profile) is not None:
        entry[ISOLATION_SCOPE_KEY] = env_isolation_scope(profile)
    if with_auth and isinstance(profile.get("codex_source"), dict):
        entry["codex_source"] = dict(profile["codex_source"])
    if with_auth and isinstance(profile.get("vars"), dict):
        # an endpoint and its key are the env's login too
        entry["vars"] = json.loads(json.dumps(profile["vars"]))
    envs[dst] = entry
    save_environments(envs)
    return dst_home


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return path.resolve() != root.resolve()


def remove_environment(name: str, *, purge: bool = False) -> None:
    """Unregister an env.

    For ``home``-type entries, the HOME directory on disk is retained by default
    so user state isn't accidentally destroyed. Pass ``purge=True`` to also
    delete the directory tree — only allowed for directories under
    ``~/.clippet/envs/``.
    """

    envs = load_environments()

    if name not in envs:
        available = ", ".join(sorted(envs.keys())) if envs else "(none)"
        raise KeyError(
            f"Environment '{name}' not found. "
            f"Available environments: {available}"
        )

    profile = envs[name]
    home_dir = Path(profile.get("home_dir") or "")
    should_purge = purge and entry_type(profile) == ENV_TYPE_HOME and home_dir.is_dir()
    if should_purge and not _is_within(home_dir, get_envs_root()):
        raise ValueError(
            f"Refusing to purge '{home_dir}': it is outside {get_envs_root()}. "
            "Remove it manually if you really want it gone."
        )

    del envs[name]
    save_environments(envs)

    if should_purge:
        shutil.rmtree(home_dir, ignore_errors=True)


def list_environments() -> dict[str, dict]:
    """Return all registered environment profiles."""

    return load_environments()


# ---------------------------------------------------------------------------
# Codex identities inside HOME envs
# ---------------------------------------------------------------------------


def codex_auth_path(home_dir: Path | str) -> Path:
    return Path(home_dir) / ".codex" / "auth.json"


def read_env_codex_credential(home_dir: Path | str):
    """Return the Codex credential stored in an env HOME, or *None*."""

    from clippet.auth.codex import CredentialFormatError, load_codex_credentials

    path = codex_auth_path(home_dir)
    if not path.is_file():
        return None
    try:
        credentials = load_codex_credentials(path)
    except (CredentialFormatError, ValueError, OSError):
        return None
    if not credentials:
        return None
    credential = credentials[0]
    if credential.auth_mode == "apikey" and not credential.base_url:
        # an API-key login's endpoint lives in the env's config.toml
        from clippet.endpoints import codex_base_url

        credential.base_url = codex_base_url(home_dir)
    return credential


def write_env_codex_credential(home_dir: Path | str, credential) -> Path:
    """Write *credential* as ``<home>/.codex/auth.json`` (atomic, ``0600``)."""

    from clippet.isolation import _write_private_file

    path = codex_auth_path(home_dir)
    _write_private_file(path, credential.to_auth_json_text().encode("utf-8"))
    return path


def _source_record(source: str, credential, *, linked: bool) -> dict[str, Any]:
    selector = credential.display_account_id or credential.source_id
    return {
        "source": source,
        "format": credential.source_format,
        "selector": selector,
        "source_id": credential.source_id,
        "email": credential.display_email,
        "linked": linked,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def import_codex_credential(
    name: str,
    credential,
    *,
    source: str,
    link: bool = False,
    overwrite: bool = False,
    seed_config: bool = True,
    description: str = "",
) -> tuple[Path, bool]:
    """Store a Codex credential in env *name*, creating the env if needed.

    Returns ``(home_dir, created)``.  An existing env that already holds a
    *different* Codex account is only replaced with ``overwrite=True``.
    """

    validate_env_name(name)
    envs = load_environments()
    created = False

    if name in envs:
        profile = envs[name]
        if entry_type(profile) != ENV_TYPE_HOME:
            raise ValueError(f"Environment '{name}' is a file alias, not a HOME env.")
        home_dir = Path(profile["home_dir"])
        home_dir.mkdir(parents=True, exist_ok=True)
        existing = read_env_codex_credential(home_dir)
        if (
            existing is not None
            and not overwrite
            and existing.identity_key() != credential.identity_key()
        ):
            raise FileExistsError(
                f"Env '{name}' already holds Codex account "
                f"{existing.display_email or existing.display_account_id or '(unknown)'}; "
                "pass --overwrite to replace it."
            )
    else:
        home_dir, _ = create_home_env_with_report(
            name,
            from_current=seed_config,
            agents=["codex"],
            description=description,
        )
        created = True

    write_env_codex_credential(home_dir, credential)
    if credential.auth_mode == "apikey" and credential.base_url:
        # auth.json has no place for it; Codex reads it from config.toml
        from clippet.endpoints import set_codex_base_url

        set_codex_base_url(home_dir, credential.base_url)
    update_environment(name, codex_source=_source_record(source, credential, linked=link))
    return home_dir, created


@dataclass
class SyncResult:
    """Outcome of :func:`sync_linked_codex_credential`."""

    action: str  # "updated" | "unchanged" | "env-newer" | "skipped" | "error"
    message: str = ""


def sync_linked_codex_credential(name: str, profile: dict) -> SyncResult:
    """Refresh an env's Codex login from its linked source (e.g. Cockpit Tools).

    Linked envs follow the source: if the source holds a newer login for the
    same account, the env's ``auth.json`` is replaced.  If the env holds the
    newer one (Codex refreshed inside the env), nothing is overwritten and the
    caller gets a warning: the source now carries a superseded refresh token.
    """

    from clippet.auth.codex import load_codex_credentials, select_credential

    record = profile.get("codex_source")
    if not isinstance(record, dict) or not record.get("linked"):
        return SyncResult("skipped")

    home_dir = Path(profile.get("home_dir") or "")
    try:
        source_credentials = load_codex_credentials(record["source"])
        credential = select_credential(source_credentials, record.get("selector"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a warning
        return SyncResult("error", f"could not read linked source: {exc}")

    current = read_env_codex_credential(home_dir)
    if current is not None and current.identity_key() != credential.identity_key():
        return SyncResult(
            "error",
            "linked source account differs from the env's account; not syncing",
        )

    def stamp(cred) -> datetime:
        return cred.effective_last_refresh or datetime.min.replace(tzinfo=timezone.utc)

    if current is not None and current.refresh_token == credential.refresh_token:
        if current.access_token == credential.access_token:
            return SyncResult("unchanged")
    if current is not None and stamp(current) > stamp(credential):
        return SyncResult(
            "env-newer",
            "Codex refreshed this login inside the env; the linked source still "
            "holds the previous refresh token and will fail to refresh it.",
        )

    write_env_codex_credential(home_dir, credential)
    return SyncResult("updated", "pulled the latest login from the linked source")

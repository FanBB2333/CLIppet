"""What the TUI shows, read from CLIppet's own state: envs, accounts, Codex
sessions, the project config and a few checks.  Nothing here imports
Textual, so it can be tested on its own."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clippet.auth.codex import CodexCredential, load_codex_credentials
from clippet.config.environments import (
    ENV_TYPE_HOME,
    EnvironmentRegistryError,
    entry_type,
    get_clippet_root,
    get_envs_root,
    get_environments_file,
    list_environments,
    read_env_codex_credential,
    shared_entries,
)
from clippet.isolation import AGENT_CONFIG_PATHS, AGENT_IDENTITY_ENV_VARS, HOME_POINTER_ENV_VARS

BASE = "base"
AGENT_BINARIES = {"codex": "codex", "claude": "claude", "gemini": "gemini", "qodercli": "qodercli"}
INSTALL_HINTS = {
    "codex": "npm i -g @openai/codex",
    "claude": "npm i -g @anthropic-ai/claude-code",
    "gemini": "npm i -g @google/gemini-cli",
    "qodercli": "see qoder.com/cli",
}


def login_state(credential: CodexCredential | None) -> str:
    """ok · apikey · stale (access token expired, refreshed on next use) · reauth · none"""

    if credential is None:
        return "none"
    if credential.requires_reauth:
        return "reauth"
    if credential.auth_mode == "apikey":
        return "apikey"
    if credential.is_expired():
        return "stale"
    return "ok"


# --- envs ---------------------------------------------------------------------


@dataclass
class EnvView:
    """One row of the env list: a registered env, or ``base`` (the real HOME)."""

    name: str
    kind: str  # "base" | "home" | "file"
    path: Path | None
    exists: bool = True
    description: str = ""
    credential: CodexCredential | None = None
    source: dict | None = None
    agents: dict[str, bool] = field(default_factory=dict)
    share: tuple[str, ...] = ()
    profile: dict = field(default_factory=dict)

    @property
    def is_base(self) -> bool:
        return self.kind == BASE

    @property
    def is_home(self) -> bool:
        return self.kind in (BASE, ENV_TYPE_HOME)

    @property
    def linked(self) -> bool:
        return bool(self.source and self.source.get("linked"))

    @property
    def login(self) -> str:
        return login_state(self.credential)

    @property
    def codex_home(self) -> Path | None:
        """Where Codex keeps its state for this env."""

        if self.kind == BASE:
            override = os.environ.get("CODEX_HOME")
            return Path(override).expanduser() if override else Path.home() / ".codex"
        if self.kind == ENV_TYPE_HOME and self.path is not None:
            return self.path / ".codex"
        return None


def _agent_dirs(home: Path) -> dict[str, bool]:
    return {agent: any((home / rel).exists() for rel in paths) for agent, paths in AGENT_CONFIG_PATHS.items()}


def base_view() -> EnvView:
    home = Path.home()
    view = EnvView(name=BASE, kind=BASE, path=home, description="your real HOME")
    codex_home = view.codex_home
    auth = codex_home / "auth.json" if codex_home else None
    if auth is not None and auth.is_file():
        try:
            credentials = load_codex_credentials(auth)
            view.credential = credentials[0] if credentials else None
        except (ValueError, OSError):
            view.credential = None
    view.agents = _agent_dirs(home)
    return view


def env_view(name: str, profile: dict) -> EnvView:
    kind = entry_type(profile)
    if kind == ENV_TYPE_HOME:
        home = Path(profile.get("home_dir") or "")
        view = EnvView(
            name=name,
            kind=kind,
            path=home,
            exists=home.is_dir(),
            description=str(profile.get("description") or ""),
            share=shared_entries(profile),
            profile=profile,
        )
        if view.exists:
            view.credential = read_env_codex_credential(home)
            view.agents = _agent_dirs(home)
        source = profile.get("codex_source")
        view.source = source if isinstance(source, dict) else None
        return view
    path = Path(profile.get("config_path") or "")
    return EnvView(
        name=name,
        kind=kind,
        path=path,
        exists=path.exists(),
        description=str(profile.get("description") or ""),
        profile=profile,
    )


def load_envs() -> tuple[list[EnvView], str | None]:
    """``base`` first, then every registered env by name; plus a registry error, if any."""

    views = [base_view()]
    try:
        envs = list_environments()
    except EnvironmentRegistryError as exc:
        return views, str(exc)
    for name in sorted(envs):
        views.append(env_view(name, envs[name]))
    return views, None


# --- Codex sessions -----------------------------------------------------------


@dataclass
class SessionView:
    id: str
    started: datetime | None
    cwd: str
    prompt: str
    origin: str
    path: Path


def _first_prompt(item: dict) -> str | None:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return None
    if item.get("type") == "event_msg" and payload.get("type") == "user_message":
        message = payload.get("message")
        return message if isinstance(message, str) else None
    if item.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
        parts = payload.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        stripped = text.lstrip()
        if stripped and not stripped.startswith("<") and not stripped.startswith("# AGENTS.md"):
            return text
    return None


def read_session(path: Path, max_lines: int = 60) -> SessionView | None:
    """The id, start, working directory and first prompt of one rollout file."""

    meta: dict[str, Any] = {}
    prompt = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for i, line in enumerate(handle):
                if i >= max_lines:
                    break
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "session_meta" and isinstance(item.get("payload"), dict):
                    meta = item["payload"]
                    continue
                text = _first_prompt(item)
                if text:
                    prompt = text
                    break
    except OSError:
        return None
    session_id = str(meta.get("id") or meta.get("session_id") or "")
    if not session_id:
        match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path.name)
        if not match:
            return None
        session_id = match.group(1)
    started = None
    stamp = meta.get("timestamp")
    if isinstance(stamp, str):
        try:
            started = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            started = None
    if started is None:
        try:
            started = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            started = None
    return SessionView(
        id=session_id,
        started=started,
        cwd=str(meta.get("cwd") or ""),
        prompt=" ".join(prompt.split()),
        origin=str(meta.get("originator") or ""),
        path=path,
    )


def codex_sessions(codex_home: Path | None, limit: int = 12) -> list[SessionView]:
    """The newest Codex sessions kept under *codex_home*, newest first."""

    if codex_home is None:
        return []
    root = codex_home / "sessions"
    if not root.is_dir():
        return []
    files = sorted(root.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.name, reverse=True)
    out = []
    for path in files[: limit * 2]:
        session = read_session(path)
        if session is not None:
            out.append(session)
        if len(out) >= limit:
            break
    return out


def count_sessions(codex_home: Path | None) -> int:
    if codex_home is None or not (codex_home / "sessions").is_dir():
        return 0
    return sum(1 for _ in (codex_home / "sessions").glob("*/*/*/rollout-*.jsonl"))


# --- accounts -----------------------------------------------------------------


@dataclass
class AccountView:
    key: str
    credential: CodexCredential
    source: str  # what load_codex_credentials() takes: "cockpit" or a path
    source_label: str
    index: int  # 1-based, within its source
    holders: list[str] = field(default_factory=list)

    @property
    def login(self) -> str:
        return login_state(self.credential)


@dataclass
class SourceView:
    source: str
    label: str
    count: int = 0
    error: str | None = None
    user_added: bool = False


def default_sources() -> list[str]:
    """Account sources found without asking: the Cockpit Tools store, when present."""

    from clippet.auth.cockpit import find_cockpit_dir

    try:
        find_cockpit_dir()
    except (FileNotFoundError, ValueError, OSError):
        return []
    return ["cockpit"]


def source_label(source: str) -> str:
    if source == "cockpit" or source.startswith("cockpit:"):
        return "Cockpit Tools"
    return Path(source).name or source


def load_accounts(sources: list[str], envs: list[EnvView], user_sources: list[str] | None = None) -> tuple[list[AccountView], list[SourceView]]:
    """Every Codex account in *sources*, with the envs that hold the same identity."""

    holders: dict[str, list[str]] = {}
    for env in envs:
        if env.credential is not None:
            holders.setdefault(env.credential.identity_key(), []).append(env.name)
    user = set(user_sources or [])
    rows: list[AccountView] = []
    reports: list[SourceView] = []
    seen: set[str] = set()
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        report = SourceView(source=source, label=source_label(source), user_added=source in user)
        reports.append(report)
        try:
            credentials = load_codex_credentials(source)
        except Exception as exc:  # noqa: BLE001 - shown next to the source
            report.error = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            continue
        report.count = len(credentials)
        for i, credential in enumerate(credentials, 1):
            rows.append(
                AccountView(
                    key=f"{source}#{i}",
                    credential=credential,
                    source=source,
                    source_label=report.label,
                    index=i,
                    holders=holders.get(credential.identity_key(), []),
                )
            )
    return rows, reports


def suggest_env_name(credential: CodexCredential, taken: set[str]) -> str:
    """A short env name for an account: the e-mail's local part, made unique."""

    base = (credential.display_email or credential.label or "codex").split("@")[0]
    base = re.sub(r"[^\w.\-]+", "-", base).strip("-.").lower() or "codex"
    if not re.match(r"^[^\W]", base):
        base = f"codex-{base}"
    name, n = base, 2
    while name in taken or name == BASE:
        name, n = f"{base}-{n}", n + 1
    return name


# --- the project --------------------------------------------------------------


@dataclass
class ProjectView:
    workspace: Path
    config_path: Path | None = None
    root: Path | None = None
    error: str | None = None
    launches: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def load_project(workspace: Path) -> ProjectView:
    """The ``.clippet.json`` that applies to *workspace* and what each agent resolves to."""

    from dataclasses import asdict

    from clippet.config.project import find_project_config, resolve_project_launch

    view = ProjectView(workspace=workspace)
    try:
        found = find_project_config(workspace)
    except Exception as exc:  # noqa: BLE001
        view.error = str(exc)
        return view
    if found is None:
        return view
    view.config_path = Path(found)
    view.root = view.config_path.parent
    try:
        view.raw = json.loads(view.config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        view.error = f"cannot read {view.config_path}: {exc}"
        return view
    for agent in ("codex", "claude", "gemini", "qodercli"):
        try:
            launch = resolve_project_launch(workspace, agent)
        except KeyError:
            continue
        except (ValueError, FileNotFoundError) as exc:
            view.launches[agent] = {"error": str(exc)}
            continue
        view.launches[agent] = {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in asdict(launch).items()
            if value not in (None, "") and key not in ("agent_type", "project_root", "config_file")
        }
    return view


def project_config_target(workspace: Path) -> Path:
    """Where a new ``.clippet.json`` goes: the Git root of *workspace*, else *workspace*."""

    from clippet.config.project import _find_git_root, find_project_config

    found = find_project_config(workspace)
    if found is not None:
        return Path(found)
    root = _find_git_root(workspace)
    return (root or workspace) / ".clippet.json"


def pin_project_env(config_path: Path, agent: str, env_name: str | None) -> dict[str, Any]:
    """Point *agent* at *env_name* in a ``.clippet.json`` (or drop it when None).

    Other agents and fields are kept as they are; the file is replaced atomically.
    """

    from clippet.isolation import _write_private_file

    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{config_path} is not a JSON object")
    else:
        data = {"version": 1, "agents": {}}
    agents = data.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise ValueError(f"'agents' in {config_path} is not an object")
    if env_name:
        agents[agent] = {"env": env_name}
    else:
        agents.pop(agent, None)
    _write_private_file(config_path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.chmod(config_path, 0o644)
    return data


# --- checks -------------------------------------------------------------------


@dataclass
class Check:
    section: str
    label: str
    state: str  # ok | warn | bad | info
    detail: str = ""


def agent_version(binary: str, timeout: float = 6.0) -> tuple[str | None, str | None]:
    """``(path, version)`` of an agent CLI; version is None when it would not say."""

    path = shutil.which(binary)
    if path is None:
        return None, None
    try:
        done = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return path, None
    text = (done.stdout or done.stderr or "").strip().splitlines()
    return path, (text[0].strip() if text else None)


def scrubbed_variables(environ: dict[str, str] | None = None) -> list[str]:
    """Names (never values) of variables in this shell that isolated runs drop."""

    from fnmatch import fnmatchcase

    environ = dict(os.environ if environ is None else environ)
    patterns = list(HOME_POINTER_ENV_VARS)
    for names in AGENT_IDENTITY_ENV_VARS.values():
        patterns.extend(names)
    return sorted(name for name in environ if any(fnmatchcase(name, pat) for pat in patterns))


def run_checks(envs: list[EnvView], versions: dict[str, tuple[str | None, str | None]]) -> list[Check]:
    from clippet import __version__

    checks: list[Check] = []
    for agent in AGENT_BINARIES:
        path, version = versions.get(agent, (None, None))
        if path is None:
            checks.append(Check("Agents", agent, "bad", f"not on PATH — {INSTALL_HINTS[agent]}"))
        else:
            checks.append(Check("Agents", agent, "ok", f"{version or 'version unknown'} · {path}"))

    root = get_clippet_root()
    checks.append(Check("CLIppet", "version", "info", __version__))
    checks.append(Check("CLIppet", "home", "info", f"{root}" + (" (CLIPPET_HOME)" if os.environ.get("CLIPPET_HOME") else "")))
    registry = get_environments_file()
    registered = [e for e in envs if not e.is_base]
    checks.append(Check("CLIppet", "registry", "ok" if registry.exists() or not registered else "warn",
                        f"{registry} · {len(registered)} env(s)"))
    checks.append(Check("CLIppet", "envs root", "info", str(get_envs_root())))

    try:
        from clippet.auth.cockpit import find_cockpit_dir

        cockpit_dir = find_cockpit_dir()
        checks.append(Check("Accounts", "Cockpit Tools", "ok", str(cockpit_dir)))
    except (FileNotFoundError, ValueError, OSError):
        checks.append(Check("Accounts", "Cockpit Tools", "info", "no data directory found (set CLIPPET_COCKPIT_DIR)"))
    try:
        import cryptography  # noqa: F401

        checks.append(Check("Accounts", "decrypting the store", "ok", "cryptography is installed"))
    except ImportError:
        checks.append(Check("Accounts", "decrypting the store", "warn", "pip install 'clippet[cockpit]'"))

    dropped = scrubbed_variables()
    checks.append(Check(
        "Isolation", "variables dropped for env runs", "info" if dropped else "ok",
        ", ".join(dropped) if dropped else "none set in this shell",
    ))

    for env in registered:
        label = env.name
        if not env.exists:
            checks.append(Check("Envs", label, "bad", f"missing: {env.path}"))
            continue
        if env.kind != ENV_TYPE_HOME:
            continue
        broken = [entry for entry in env.share
                  if (env.path / entry).is_symlink() and not (env.path / entry).exists()]
        if broken:
            checks.append(Check("Envs", label, "warn", f"broken shared link: {', '.join(broken)}"))
        state = env.login
        if state == "reauth":
            checks.append(Check("Envs", label, "bad", "Codex login needs to be renewed"))
        elif state == "stale":
            checks.append(Check("Envs", label, "info", "Codex access token expired; it is refreshed on the next run"))
        elif state in ("ok", "apikey"):
            checks.append(Check("Envs", label, "ok", f"Codex: {env.credential.display_email or 'API key'}"))
    return checks

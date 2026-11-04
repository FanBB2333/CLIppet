"""Third-party API endpoints for the agents in an env.

Any agent can be pointed at an API other than its maker's (a relay, another
provider's compatible API, a local server), each in its own way; see
:class:`clippet.agents.EndpointSpec`.  An env keeps, per agent, the variables
CLIppet sets for that agent's runs (``vars`` in environments.json).  A value
may name a variable of CLIppet's own environment as ``${NAME}``, so a key can
stay in your shell profile or keychain instead of a file.  :func:`set_endpoint`
writes those variables, and whatever else the agent needs, into the env.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from clippet.agents import AgentSpec, EndpointSpec, agent_spec

#: The provider CLIppet writes into Codex's config.toml and pi's models.json.
PROVIDER_ID = "clippet"

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PI_APIS = {
    "openai": "openai-completions",
    "openai-responses": "openai-responses",
    "anthropic": "anthropic-messages",
    "gemini": "google-generative-ai",
}
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.IGNORECASE)
# what pi gets as the key of a keyless endpoint: it lists a provider's models
# only when it has a key, and a local server takes any
_PI_NO_KEY = "none"


class MissingVariable(ValueError):
    """A ``${NAME}`` an env needs is not set where CLIppet runs."""


@dataclass
class Endpoint:
    """Where an agent in an env sends its requests instead of its maker's API."""

    url: str
    key: str | None = None  # how its key is given, said to people (never the key)
    model: str | None = None
    source: str = ""  # where CLIppet found it


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


def references(value: str) -> list[str]:
    """The ``${NAME}`` variables *value* names."""

    return _REFERENCE.findall(value)


def resolve_vars(
    values: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
    *,
    where: str = "",
) -> dict[str, str]:
    """*values* with each ``${NAME}`` replaced by NAME's value in *environ* (default: ours)."""

    environ = os.environ if environ is None else environ
    missing = sorted({name for value in values.values() for name in references(value) if not environ.get(name)})
    if missing:
        needed = ", ".join(f"${name}" for name in missing)
        raise MissingVariable(
            f"{where or 'this env'} takes {needed} from your environment, but it is not set here; "
            "export it (for example in ~/.secrets.zsh) and start CLIppet again"
        )
    return {key: _REFERENCE.sub(lambda match: environ[match.group(1)], value) for key, value in values.items()}


def is_secret(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


def shown(name: str, value: str) -> str:
    """*value* as it may be shown: references as written, a literal key only as set."""

    if references(value) or not is_secret(name):
        return value
    return "(set)"


def run_vars(home_dir: Path | str, agent: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables the env living in *home_dir* sets for *agent*'s runs, resolved."""

    from clippet.config.environments import env_vars, profile_for_home

    found = profile_for_home(home_dir)
    if found is None:
        return {}
    name, profile = found
    return resolve_vars(env_vars(profile, agent), environ, where=f"env '{name}' ({agent})")


# ---------------------------------------------------------------------------
# Writing an endpoint into an env
# ---------------------------------------------------------------------------


def endpoint_variables(endpoint: EndpointSpec) -> set[str]:
    """Every variable *endpoint* may set (what clearing it removes)."""

    names = {endpoint.base_url, endpoint.model, *endpoint.key}
    for variables in endpoint.apis.values():
        names.update(variables)
    names.discard("")
    return names


def _spec(agent: str) -> AgentSpec:
    spec = agent_spec(agent)
    if spec is None:
        raise ValueError(f"CLIppet does not know the agent '{agent}'")
    return spec


def _home_of(name: str) -> Path:
    from clippet.config.environments import ENV_TYPE_HOME, entry_type, get_environment

    profile = get_environment(name)
    if entry_type(profile) != ENV_TYPE_HOME or not profile.get("home_dir"):
        raise ValueError(f"env '{name}' is a config-file alias; endpoints are kept in HOME envs")
    home = Path(profile["home_dir"])
    if not home.is_dir():
        raise FileNotFoundError(f"the HOME of env '{name}' is missing: {home}")
    return home


def set_endpoint(
    name: str,
    agent: str,
    url: str,
    *,
    key_env: str | None = None,
    model: str | None = None,
    api: str | None = None,
) -> list[str]:
    """Point *agent* in env *name* at the API at *url*; returns what else to know.

    *key_env* names the variable of your environment that holds the key
    (stored as ``${key_env}``, read at each launch); without it the endpoint
    is used without a key.  *api* is the style the endpoint speaks (default:
    the agent's first).
    """

    from clippet.config.environments import set_env_vars

    spec = _spec(agent)
    endpoint = spec.endpoint
    if not endpoint.apis:
        raise ValueError(endpoint.note or f"{spec.title} cannot be pointed at another API by CLIppet")
    api = api or next(iter(endpoint.apis))
    if api not in endpoint.apis:
        raise ValueError(f"{spec.title} speaks {', '.join(endpoint.apis)} to an endpoint, not {api}")
    if not urlparse(url).scheme or not urlparse(url).netloc:
        raise ValueError(f"not a URL: {url}")
    if key_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError(f"--key-env takes the name of a variable, not {key_env!r}")
    if endpoint.file == "pi" and not model:
        raise ValueError("pi lists the models of an endpoint it does not know: give --model")
    if spec.key == "copilot" and not model:
        raise ValueError("Copilot CLI needs the model to use with its own provider: give --model")

    home = _home_of(name)
    variables: dict[str, str] = dict(endpoint.apis[api])
    if endpoint.base_url:
        variables[endpoint.base_url] = url
    if key_env and endpoint.key:
        variables[endpoint.key[0]] = "${" + key_env + "}"
    elif key_env:
        # the agent's own file names the variable (Codex's env_key, pi's
        # "$VAR"), so the Codex app finds it in your login shell too; the
        # env only keeps it from being dropped in its runs
        variables[key_env] = "${" + key_env + "}"
    if model and endpoint.model:
        variables[endpoint.model] = model

    stale = endpoint_variables(endpoint) | {_file_key(home, endpoint)} - {""}
    if endpoint.file == "codex":
        _write_codex_provider(home, url, key_env=key_env, model=model)
    elif endpoint.file == "pi":
        _write_pi_provider(home, url, api=api, key_env=key_env, model=model or "")
    if endpoint.settings is not None:
        _merge_json(home / endpoint.settings[0], endpoint.settings[1])
    set_env_vars(name, spec.key, variables, unset=sorted(stale - variables.keys()))

    notes = [endpoint.note] if endpoint.note else []
    if model and not endpoint.model and not endpoint.file:
        notes.append(f"{spec.title} takes no model from a variable; pass one when you launch it")
    notes.extend(_conflicts(home, spec))
    return notes


def clear_endpoint(name: str, agent: str) -> None:
    """Undo :func:`set_endpoint` for *agent* in env *name*."""

    from clippet.config.environments import set_env_vars

    spec = _spec(agent)
    endpoint = spec.endpoint
    home = _home_of(name)
    stale = endpoint_variables(endpoint) | {_file_key(home, endpoint)} - {""}
    if endpoint.file == "codex":
        _clear_codex_provider(home)
    elif endpoint.file == "pi":
        _clear_pi_provider(home)
    if endpoint.settings is not None:
        path, keys = endpoint.settings
        data = _read_json(home / path)
        if data is not None and any(data.get(key) == value for key, value in keys.items()):
            for key, value in keys.items():
                if data.get(key) == value:
                    data.pop(key)
            _write_json(home / path, data)
    set_env_vars(name, spec.key, unset=sorted(stale))


def _file_key(home: Path, endpoint: EndpointSpec) -> str:
    """The variable the endpoint CLIppet wrote into the agent's own file takes its key from."""

    if endpoint.file == "codex":
        config = _read_toml(home / ".codex" / "config.toml") or {}
        providers = config.get("model_providers") if isinstance(config.get("model_providers"), dict) else {}
        provider = providers.get(PROVIDER_ID) if isinstance(providers.get(PROVIDER_ID), dict) else {}
        env_key = provider.get("env_key")
        return env_key if isinstance(env_key, str) else ""
    if endpoint.file == "pi":
        models = _read_json_quietly(home / ".pi" / "agent" / "models.json")
        providers = models.get("providers") if isinstance(models, dict) else None
        provider = providers.get(PROVIDER_ID) if isinstance(providers, dict) else None
        api_key = provider.get("apiKey") if isinstance(provider, dict) else None
        if isinstance(api_key, str) and api_key.startswith("$"):
            return api_key.lstrip("${").rstrip("}")
    return ""


def _conflicts(home: Path, spec: AgentSpec) -> list[str]:
    """What in the env's own files would override the endpoint's variables."""

    notes: list[str] = []
    if spec.key == "claude":
        settings = _read_json_quietly(home / ".claude" / "settings.json")
        block = settings.get("env") if isinstance(settings, dict) else None
        if isinstance(block, dict):
            names = sorted(key for key in block if key in endpoint_variables(spec.endpoint))
            if names:
                notes.append(f"the env's .claude/settings.json sets {', '.join(names)} in \"env\", which wins")
    if spec.key == "copilot":
        providers = _read_json_quietly(home / ".copilot" / "providers.json")
        if isinstance(providers, dict) and (providers.get("providers") or providers.get("models")):
            notes.append("the env's .copilot/providers.json declares providers, which Copilot CLI prefers")
    return notes


# ---------------------------------------------------------------------------
# Reading what an env points at
# ---------------------------------------------------------------------------


def endpoint_of(home_dir: Path | str, agent: str, variables: Mapping[str, str] | None = None) -> Endpoint | None:
    """Where *agent* in the HOME at *home_dir* sends requests, if not to its maker.

    *variables* are the env's stored variables for the agent (default: looked
    up in the registry).  Keys are never read, only how they are given.
    """

    spec = agent_spec(agent)
    if spec is None:
        return None
    home = Path(home_dir)
    if variables is None:
        from clippet.config.environments import env_vars, profile_for_home

        found = profile_for_home(home)
        variables = env_vars(found[1], spec.key) if found else {}
    endpoint = spec.endpoint
    if endpoint.base_url and variables.get(endpoint.base_url):
        return Endpoint(
            url=variables[endpoint.base_url],
            key=_key_shown(endpoint, variables),
            model=variables.get(endpoint.model) if endpoint.model else None,
            source="env vars",
        )
    if spec.key == "claude":
        return _from_env_block(home / ".claude" / "settings.json", endpoint, ".claude/settings.json")
    if spec.key == "codex":
        return _codex_endpoint(home, variables)
    if spec.key == "pi":
        return _pi_endpoint(home, variables)
    if spec.key == "copilot":
        return _copilot_endpoint(home)
    return None


def _key_shown(endpoint: EndpointSpec, variables: Mapping[str, str]) -> str | None:
    for name in endpoint.key:
        value = variables.get(name)
        if value:
            refs = references(value)
            return f"${refs[0]}" if refs and value == "${" + refs[0] + "}" else f"{name} (set)"
    return None


def _from_env_block(path: Path, endpoint: EndpointSpec, source: str) -> Endpoint | None:
    data = _read_json_quietly(path)
    block = data.get("env") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not block.get(endpoint.base_url):
        return None
    key = next((f"{name} in {source}" for name in endpoint.key if block.get(name)), None)
    return Endpoint(url=str(block[endpoint.base_url]), key=key,
                    model=str(block.get(endpoint.model) or "") or None, source=source)


def _codex_endpoint(home: Path, variables: Mapping[str, str]) -> Endpoint | None:
    config = _read_toml(home / ".codex" / "config.toml")
    if not config:
        return None
    model = config.get("model") if isinstance(config.get("model"), str) else None
    provider_id = config.get("model_provider")
    providers = config.get("model_providers") if isinstance(config.get("model_providers"), dict) else {}
    provider = providers.get(provider_id) if isinstance(provider_id, str) else None
    if isinstance(provider, dict) and isinstance(provider.get("base_url"), str):
        env_key = provider.get("env_key")
        key = None
        if isinstance(env_key, str):
            stored = variables.get(env_key, "")
            refs = references(stored)
            key = f"${refs[0]}" if refs else f"${env_key}"
        return Endpoint(url=provider["base_url"], key=key, model=model,
                        source=f".codex/config.toml [model_providers.{provider_id}]")
    if isinstance(config.get("openai_base_url"), str):
        return Endpoint(url=config["openai_base_url"], key="the env's API-key login", model=model,
                        source=".codex/config.toml openai_base_url")
    return None


def _pi_endpoint(home: Path, variables: Mapping[str, str]) -> Endpoint | None:
    settings = _read_json_quietly(home / ".pi" / "agent" / "settings.json")
    models = _read_json_quietly(home / ".pi" / "agent" / "models.json")
    provider_id = settings.get("defaultProvider") if isinstance(settings, dict) else None
    providers = models.get("providers") if isinstance(models, dict) else None
    provider = providers.get(provider_id) if isinstance(providers, dict) and isinstance(provider_id, str) else None
    if not isinstance(provider, dict) or not isinstance(provider.get("baseUrl"), str):
        return None
    api_key = provider.get("apiKey")
    key = None
    if isinstance(api_key, str) and api_key.startswith("$"):
        own = api_key.lstrip("${").rstrip("}")
        refs = references(variables.get(own, ""))
        key = f"${refs[0]}" if refs else f"${own}"
    elif isinstance(api_key, str) and api_key.startswith("!"):
        key = "a command in models.json"
    elif api_key and api_key != _PI_NO_KEY:
        key = "in models.json"
    model = settings.get("defaultModel") if isinstance(settings, dict) else None
    return Endpoint(url=provider["baseUrl"], key=key, model=model if isinstance(model, str) else None,
                    source=f".pi/agent/models.json [{provider_id}]")


def _copilot_endpoint(home: Path) -> Endpoint | None:
    data = _read_json_quietly(home / ".copilot" / "providers.json")
    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if isinstance(provider, dict) and isinstance(provider.get("baseUrl"), str):
            has_key = bool(provider.get("apiKey") or provider.get("bearerToken"))
            return Endpoint(url=provider["baseUrl"], key="in providers.json" if has_key else None,
                            source=".copilot/providers.json")
    return None


# ---------------------------------------------------------------------------
# Codex: config.toml
# ---------------------------------------------------------------------------

_TABLE_LINE = re.compile(r"^\s*\[")


def _toml_set_top(text: str, key: str, value: str | None) -> str:
    """*text* with top-level *key* set to the TOML *value* (or removed when None)."""

    lines = text.splitlines()
    first_table = next((index for index, line in enumerate(lines) if _TABLE_LINE.match(line)), len(lines))
    own = re.compile(rf"^\s*{re.escape(key)}\s*=")
    top = [line for line in lines[:first_table] if not own.match(line)]
    if value is not None:
        at = len(top)
        while at and not top[at - 1].strip():
            at -= 1
        top.insert(at, f"{key} = {value}")
    return "\n".join(top + lines[first_table:]).strip("\n") + "\n"


def _toml_set_table(text: str, table: str, body: list[str] | None) -> str:
    """*text* without table ``[table]``, and with *body* as that table at its end (unless None)."""

    header = re.compile(rf"^\s*\[\s*{re.escape(table)}\s*\]\s*(#.*)?$")
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if _TABLE_LINE.match(line):
            inside = bool(header.match(line))
        if not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    if body is not None:
        kept += ([""] if kept else []) + [f"[{table}]", *body]
    return "\n".join(kept) + "\n" if kept else ""


def _check_toml(text: str, path: Path) -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        return
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"could not edit {path} safely ({exc}); edit it by hand") from exc


def _read_toml(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _edit_toml(path: Path, edit) -> None:
    from clippet.isolation import _write_private_file

    try:
        before = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        before = ""
    _check_toml(before, path)
    after = edit(before)
    _check_toml(after, path)
    if after != before:
        _write_private_file(path, after.encode("utf-8"))


def _write_codex_provider(home: Path, url: str, *, key_env: str | None, model: str | None) -> None:
    host = urlparse(url).netloc or url
    body = [f"name = {json.dumps(host)}", f"base_url = {json.dumps(url)}"]
    if key_env:
        body.append(f"env_key = {json.dumps(key_env)}")
    body.append('wire_api = "responses"')

    def edit(text: str) -> str:
        text = _toml_set_top(text, "model_provider", json.dumps(PROVIDER_ID))
        if model:
            text = _toml_set_top(text, "model", json.dumps(model))
        return _toml_set_table(text, f"model_providers.{PROVIDER_ID}", body)

    _edit_toml(home / ".codex" / "config.toml", edit)


def read_codex_config(home: Path | str) -> dict[str, Any]:
    """The Codex config.toml in the HOME at *home* ({} when there is none); refuses one that is not TOML."""

    path = Path(home) / ".codex" / "config.toml"
    if not path.is_file():
        return {}
    config = _read_toml(path)
    if config is None:
        raise ValueError(f"{path} is not valid TOML; edit it by hand")
    return config


def set_codex_config(home: Path | str, key: str, value: str | None) -> None:
    """Set top-level *key* of the Codex config in the HOME at *home* to the TOML *value* (None: remove it)."""

    _edit_toml(Path(home) / ".codex" / "config.toml", lambda text: _toml_set_top(text, key, value))


def set_codex_base_url(home: Path | str, url: str | None) -> None:
    """Make Codex's own provider in the HOME at *home* use *url* (``openai_base_url``)."""

    path = Path(home) / ".codex" / "config.toml"
    if url is None and not path.is_file():
        return
    _edit_toml(path, lambda text: _toml_set_top(text, "openai_base_url", json.dumps(url) if url else None))


def codex_base_url(home: Path | str) -> str | None:
    """The ``openai_base_url`` of the Codex config in the HOME at *home*."""

    config = _read_toml(Path(home) / ".codex" / "config.toml") or {}
    url = config.get("openai_base_url")
    return url if isinstance(url, str) and url else None


def _clear_codex_provider(home: Path) -> None:
    path = home / ".codex" / "config.toml"
    config = _read_toml(path) or {}

    def edit(text: str) -> str:
        if config.get("model_provider") == PROVIDER_ID:
            text = _toml_set_top(text, "model_provider", None)
        return _toml_set_table(text, f"model_providers.{PROVIDER_ID}", None)

    if path.is_file():
        _edit_toml(path, edit)


# ---------------------------------------------------------------------------
# pi: models.json and settings.json
# ---------------------------------------------------------------------------


def _write_pi_provider(home: Path, url: str, *, api: str, key_env: str | None, model: str) -> None:
    models_path = home / ".pi" / "agent" / "models.json"
    models = _read_json(models_path) or {}
    providers = models.get("providers")
    if not isinstance(providers, dict):
        providers = models["providers"] = {}
    providers[PROVIDER_ID] = {
        "name": urlparse(url).netloc or url,
        "baseUrl": url,
        "api": _PI_APIS[api],
        "apiKey": "$" + key_env if key_env else _PI_NO_KEY,
        "models": [{"id": model}],
    }
    _write_json(models_path, models)
    _merge_json(home / ".pi" / "agent" / "settings.json", {"defaultProvider": PROVIDER_ID, "defaultModel": model})


def _clear_pi_provider(home: Path) -> None:
    models_path = home / ".pi" / "agent" / "models.json"
    models = _read_json(models_path)
    if models is not None and isinstance(models.get("providers"), dict) and PROVIDER_ID in models["providers"]:
        models["providers"].pop(PROVIDER_ID)
        _write_json(models_path, models)
    settings_path = home / ".pi" / "agent" / "settings.json"
    settings = _read_json(settings_path)
    if settings is not None and settings.get("defaultProvider") == PROVIDER_ID:
        settings.pop("defaultProvider")
        settings.pop("defaultModel", None)
        _write_json(settings_path, settings)


# ---------------------------------------------------------------------------
# JSON files
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """The JSON object in *path*, None when there is no file; refuses anything else."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not plain JSON ({exc}); edit it by hand") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object; edit it by hand")  # noqa: TRY004 - a file's content
    return data


def _read_json_quietly(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    from clippet.isolation import _write_private_file

    _write_private_file(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _merge_json(path: Path, values: dict[str, Any]) -> None:
    data = _read_json(path) or {}
    if any(data.get(key) != value for key, value in values.items()):
        data.update(values)
        _write_json(path, data)


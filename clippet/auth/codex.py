"""Codex (OpenAI) credentials in every common storage format.

Codex CLI reads its identity from ``$CODEX_HOME/auth.json``.  Account
managers store the very same OAuth tokens in their own layouts.  This module
normalises all of them into :class:`CodexCredential` and renders Codex's
native ``auth.json`` from it, so any of these can be handed to CLIppet:

* Codex CLI ``auth.json`` (ChatGPT login or API key)
* Cockpit Tools exports / auto-backups (``cockpit-tools.data-transfer`` and
  ``cockpit-tools.account-transfer``) and single Cockpit account objects
* Cockpit Tools' live account store (see :mod:`clippet.auth.cockpit`)
* sub2api account exports (``credentials`` blocks of OpenAI OAuth accounts)
* CLIProxyAPI / CPA token files (``{"type": "codex", "access_token": ...}``)
* Generic token bundles (``id_token`` / ``access_token`` / ``refresh_token``),
  JSON arrays of any of the above, and JSON Lines files

Nothing here talks to the network.  JWT claims are decoded locally (without
signature verification) only to label accounts.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from clippet.auth.jwt import claim_time, decode_jwt_claims, encode_unsigned_jwt

FORMAT_NATIVE = "codex-auth"
FORMAT_COCKPIT_EXPORT = "cockpit-export"
FORMAT_COCKPIT_ACCOUNT = "cockpit-account"
FORMAT_COCKPIT_STORE = "cockpit-store"
FORMAT_SUB2API = "sub2api"
FORMAT_CLIPROXYAPI = "cliproxyapi"
FORMAT_TOKENS = "oauth-tokens"

AUTH_MODE_CHATGPT = "chatgpt"
AUTH_MODE_APIKEY = "apikey"

_OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
_OPENAI_PROFILE_CLAIM = "https://api.openai.com/profile"

# Keys Codex CLI itself writes at the top level of auth.json.
_NATIVE_TOP_LEVEL_KEYS = {
    "auth_mode",
    "OPENAI_API_KEY",
    "tokens",
    "last_refresh",
    "agent_identity",
    "personal_access_token",
    "bedrock_api_key",
    "bedrock_access_keys",
}

# Credential kinds Codex can use without a ``tokens`` block; native files
# holding them are passed through verbatim.
_NATIVE_OTHER_CREDENTIALS = (
    "personal_access_token",
    "agent_identity",
    "bedrock_api_key",
    "bedrock_access_keys",
)

# Provider markers of *other* agents that share the same export files
# (sub2api, CLIProxyAPI and Cockpit also manage Claude/Gemini/... accounts).
_NON_CODEX_PROVIDERS = frozenset({
    "anthropic", "claude", "claude-code", "gemini", "gemini-cli", "google",
    "vertex", "aistudio", "antigravity", "qwen", "iflow", "kiro", "cursor",
    "windsurf", "copilot", "github-copilot", "codebuddy", "qoder", "trae",
    "zed", "workbuddy",
})

# Containers under which other tools nest lists/maps of accounts.
_CONTAINER_KEYS = (
    "accounts",
    "exported_data",
    "data",
    "items",
    "list",
    "records",
    "credentials_list",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "id_token": ("id_token", "idToken"),
    "access_token": ("access_token", "accessToken"),
    "refresh_token": ("refresh_token", "refreshToken"),
    "account_id": (
        "account_id",
        "accountId",
        "chatgpt_account_id",
        "chatgptAccountId",
    ),
    "api_key": ("OPENAI_API_KEY", "openai_api_key", "api_key", "apiKey"),
    "base_url": (
        "OPENAI_BASE_URL",
        "base_url",
        "baseUrl",
        "api_base_url",
        "apiBaseUrl",
    ),
    "email": ("email", "account_email", "user_email"),
    "plan_type": ("plan_type", "planType", "chatgpt_plan_type", "plan"),
    "label": ("name", "label", "remark", "note", "nickname"),
    "id": ("id", "uuid"),
}


class CredentialFormatError(ValueError):
    """Raised when a source does not contain any usable Codex credential."""


class CredentialSelectionError(ValueError):
    """Raised when an account selector matches zero or several credentials."""


@dataclass(repr=False)
class CodexCredential:
    """One Codex identity, independent of where it was stored.

    ``auth_mode`` is ``"chatgpt"`` (OAuth tokens from a ChatGPT login) or
    ``"apikey"`` (an OpenAI-compatible API key, optionally with a base URL).
    """

    auth_mode: str
    api_key: str | None = None
    id_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    account_id: str | None = None
    last_refresh: datetime | None = None
    email: str | None = None
    plan_type: str | None = None
    base_url: str | None = None
    label: str | None = None
    source_format: str = "unknown"
    source_path: str | None = None
    source_id: str | None = None
    requires_reauth: bool = False
    reauth_reason: str | None = None
    is_current: bool = False
    # The original native auth.json document, kept verbatim so that a native
    # file round-trips without losing fields newer Codex versions add.
    native_document: dict[str, Any] | None = field(default=None, compare=False)

    # -- presentation -----------------------------------------------------

    def __repr__(self) -> str:  # never leak tokens through repr()
        return (
            f"CodexCredential(auth_mode={self.auth_mode!r}, "
            f"email={self.display_email!r}, account_id={self.display_account_id!r}, "
            f"source_format={self.source_format!r})"
        )

    @property
    def id_claims(self) -> dict[str, Any]:
        return decode_jwt_claims(self.id_token) or {}

    @property
    def access_claims(self) -> dict[str, Any]:
        return decode_jwt_claims(self.access_token) or {}

    def _openai_claim(self, key: str) -> Any:
        for claims in (self.id_claims, self.access_claims):
            auth = claims.get(_OPENAI_AUTH_CLAIM)
            if isinstance(auth, dict) and auth.get(key):
                return auth[key]
        return None

    @property
    def display_email(self) -> str | None:
        if self.email:
            return self.email
        email = self.id_claims.get("email")
        if isinstance(email, str) and email:
            return email
        profile = self.access_claims.get(_OPENAI_PROFILE_CLAIM)
        if isinstance(profile, dict) and isinstance(profile.get("email"), str):
            return profile["email"]
        return None

    @property
    def display_plan(self) -> str | None:
        return self.plan_type or self._openai_claim("chatgpt_plan_type")

    @property
    def display_account_id(self) -> str | None:
        return self.account_id or self._openai_claim("chatgpt_account_id")

    @property
    def access_expires_at(self) -> datetime | None:
        return claim_time(self.access_claims, "exp")

    @property
    def effective_last_refresh(self) -> datetime | None:
        """``last_refresh`` from the source, else the access token's ``iat``."""

        return self.last_refresh or claim_time(self.access_claims, "iat")

    def is_expired(self, now: datetime | None = None) -> bool:
        expires = self.access_expires_at
        if expires is None:
            return False
        return expires <= (now or datetime.now(timezone.utc))

    def identity_key(self) -> str | None:
        """A stable, non-secret key identifying the account (for de-duplication)."""

        if self.auth_mode == AUTH_MODE_APIKEY:
            return None
        account = self.display_account_id
        email = self.display_email
        if account or email:
            return f"{account or ''}|{(email or '').lower()}"
        return None

    def summary(self) -> dict[str, Any]:
        """Non-secret description suitable for printing."""

        expires = self.access_expires_at
        refreshed = self.effective_last_refresh
        return {
            "auth_mode": self.auth_mode,
            "email": self.display_email,
            "plan": self.display_plan,
            "account_id": self.display_account_id,
            "label": self.label,
            "base_url": self.base_url,
            "access_expires_at": expires.isoformat() if expires else None,
            "last_refresh": refreshed.isoformat() if refreshed else None,
            "requires_reauth": self.requires_reauth,
            "reauth_reason": self.reauth_reason,
            "source_format": self.source_format,
            "source_id": self.source_id,
            "is_current": self.is_current,
        }

    # -- rendering --------------------------------------------------------

    def to_auth_json(self) -> dict[str, Any]:
        """Render Codex CLI's native ``auth.json`` document."""

        if self.native_document is not None:
            return copy.deepcopy(self.native_document)

        if self.auth_mode == AUTH_MODE_APIKEY:
            if not self.api_key:
                raise CredentialFormatError("API-key credential has no key")
            return {"auth_mode": AUTH_MODE_APIKEY, "OPENAI_API_KEY": self.api_key}

        if not self.access_token:
            raise CredentialFormatError("ChatGPT credential has no access_token")

        tokens: dict[str, Any] = {
            "id_token": self.id_token or self._synthesized_id_token(),
            "access_token": self.access_token,
            "refresh_token": self.refresh_token or "",
        }
        account_id = self.display_account_id
        if account_id:
            tokens["account_id"] = account_id

        document: dict[str, Any] = {
            "auth_mode": AUTH_MODE_CHATGPT,
            "OPENAI_API_KEY": None,
            "tokens": tokens,
        }
        refreshed = self.effective_last_refresh
        if refreshed is not None:
            document["last_refresh"] = format_timestamp(refreshed)
        return document

    def to_auth_json_text(self) -> str:
        return json.dumps(self.to_auth_json(), indent=2) + "\n"

    def _synthesized_id_token(self) -> str:
        """Unsigned id_token carrying the claims Codex displays (email, plan)."""

        access = self.access_claims
        auth = dict(access.get(_OPENAI_AUTH_CLAIM) or {})
        if self.account_id:
            auth.setdefault("chatgpt_account_id", self.account_id)
        if self.plan_type:
            auth.setdefault("chatgpt_plan_type", self.plan_type)
        claims: dict[str, Any] = {_OPENAI_AUTH_CLAIM: auth}
        email = self.display_email
        if email:
            claims["email"] = email
        for key in ("iat", "exp", "sub"):
            if key in access:
                claims[key] = access[key]
        return encode_unsigned_jwt(claims)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse unix seconds / milliseconds or an ISO-8601 string into aware UTC."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            return None
        if seconds > 1e12:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return parse_timestamp(int(text))
        text = _FRACTION_RE.sub(r"\1", text)
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def format_timestamp(moment: datetime) -> str:
    """Format like Codex CLI does: RFC 3339 UTC with microseconds and ``Z``."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _pick(obj: dict[str, Any] | None, name: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for alias in _ALIASES[name]:
        value = obj.get(alias)
        if value not in (None, ""):
            return value
    return None


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_native_document(obj: dict[str, Any]) -> bool:
    # Codex always writes the OPENAI_API_KEY key (null for ChatGPT logins);
    # tolerate extra top-level keys newer Codex versions may add.
    if "OPENAI_API_KEY" not in obj and not set(obj) <= _NATIVE_TOP_LEVEL_KEYS:
        return False
    if str(obj.get("auth_mode") or "").lower() == "oauth":
        return False  # Cockpit's own account objects, not Codex's auth.json
    tokens = obj.get("tokens")
    has_tokens = isinstance(tokens, dict) and bool(_pick(tokens, "access_token"))
    has_other = any(obj.get(key) for key in _NATIVE_OTHER_CREDENTIALS)
    return has_tokens or has_other or bool(_str_or_none(obj.get("OPENAI_API_KEY")))


def _provider_markers(obj: dict[str, Any]) -> set[str]:
    markers = set()
    for key in ("platform", "provider", "type", "kind"):
        value = obj.get(key)
        if isinstance(value, str):
            markers.add(value.strip().lower())
    return markers


def _is_openai_access_token(token: str | None) -> bool | None:
    """True/False for JWT access tokens (OpenAI or not), None if not a JWT."""

    claims = decode_jwt_claims(token)
    if claims is None:
        return None
    if _OPENAI_AUTH_CLAIM in claims or _OPENAI_PROFILE_CLAIM in claims:
        return True
    issuer = str(claims.get("iss") or "")
    return "openai.com" in issuer


def _is_web_session(obj: dict[str, Any]) -> bool:
    """ChatGPT web-session dumps (``/api/auth/session``) cannot drive Codex."""

    if str(obj.get("token_source_mode") or "") == "chatgpt_web_session":
        return True
    has_refresh = bool(_pick(obj, "refresh_token")) or (
        isinstance(obj.get("tokens"), dict) and bool(_pick(obj["tokens"], "refresh_token"))
    )
    if has_refresh:
        return False
    return "sessionToken" in obj or (
        isinstance(obj.get("user"), dict) and "expires" in obj and "accessToken" in obj
    )


def _token_block(obj: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Locate the dict holding OAuth tokens; returns ``(block, container_key)``."""

    for key in ("tokens", "credentials", "token", "auth", "oauth"):
        block = obj.get(key)
        if isinstance(block, dict) and _pick(block, "access_token"):
            return block, key
    if _pick(obj, "access_token"):
        return obj, None
    return None, None


def _guess_format(obj: dict[str, Any], container_key: str | None) -> str:
    kind = str(obj.get("type") or "").lower()
    platform = str(obj.get("platform") or "").lower()
    if container_key == "credentials" or platform in {"openai", "codex", "chatgpt"}:
        return FORMAT_SUB2API
    if container_key is None and kind == "codex":
        return FORMAT_CLIPROXYAPI
    if container_key == "tokens" and (
        str(obj.get("id", "")).startswith("codex_")
        or "token_source_mode" in obj
        or "api_provider_mode" in obj
    ):
        return FORMAT_COCKPIT_ACCOUNT
    return FORMAT_TOKENS


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def credential_from_object(
    obj: Any,
    *,
    source_path: str | None = None,
    source_format: str | None = None,
    source_id: str | None = None,
) -> CodexCredential | None:
    """Build a credential from one JSON object, or return *None* if it is not one."""

    if not isinstance(obj, dict):
        return None

    if _provider_markers(obj) & _NON_CODEX_PROVIDERS:
        return None

    if _is_native_document(obj):
        tokens = obj.get("tokens") if isinstance(obj.get("tokens"), dict) else None
        api_key = _str_or_none(obj.get("OPENAI_API_KEY"))
        has_tokens = bool(tokens and _pick(tokens, "access_token"))
        declared = str(obj.get("auth_mode") or "")
        if has_tokens and declared.lower() != AUTH_MODE_APIKEY:
            auth_mode = AUTH_MODE_CHATGPT if declared in ("", AUTH_MODE_CHATGPT) else declared
        elif api_key:
            auth_mode = AUTH_MODE_APIKEY
        else:
            # personalAccessToken / agentIdentity / bedrock*: passed through
            auth_mode = declared or next(
                key for key in _NATIVE_OTHER_CREDENTIALS if obj.get(key)
            )
        return CodexCredential(
            auth_mode=auth_mode,
            api_key=api_key,
            id_token=_str_or_none(_pick(tokens, "id_token")),
            access_token=_str_or_none(_pick(tokens, "access_token")),
            refresh_token=_str_or_none(_pick(tokens, "refresh_token")),
            account_id=_str_or_none(_pick(tokens, "account_id")),
            last_refresh=parse_timestamp(obj.get("last_refresh")),
            base_url=_str_or_none(_pick(obj, "base_url")),
            source_format=source_format or FORMAT_NATIVE,
            source_path=source_path,
            source_id=source_id,
            native_document=copy.deepcopy(obj),
        )

    if _is_web_session(obj):
        return None

    block, container_key = _token_block(obj)
    if block is not None:
        trusted = (
            source_format in (FORMAT_COCKPIT_EXPORT, FORMAT_COCKPIT_STORE)
            or bool(_provider_markers(obj) & {"codex", "openai", "chatgpt"})
        )
        is_openai = _is_openai_access_token(_str_or_none(_pick(block, "access_token")))
        if is_openai is False or (is_openai is None and not trusted):
            # Not an OpenAI token (or no way to tell): never feed it to Codex.
            return None
    meta_sources = [obj]
    if block is not None and block is not obj:
        meta_sources.append(block)
    extra = obj.get("extra")
    if isinstance(extra, dict):
        meta_sources.append(extra)

    def meta(name: str) -> Any:
        for source in meta_sources:
            value = _pick(source, name)
            if value not in (None, ""):
                return value
        return None

    if block is None:
        # API-key style entries (e.g. relay/gateway accounts).
        api_key = _str_or_none(meta("api_key"))
        credentials = obj.get("credentials")
        if not api_key and isinstance(credentials, dict):
            api_key = _str_or_none(_pick(credentials, "api_key"))
            if api_key:
                meta_sources.append(credentials)
        if not api_key:
            return None
        api_signal = (
            source_format in (FORMAT_COCKPIT_EXPORT, FORMAT_COCKPIT_STORE)
            or bool(_provider_markers(obj) & {"codex", "openai", "chatgpt"})
            or "openai_api_key" in obj
            or str(obj.get("auth_mode") or obj.get("authMode") or "").lower() == "apikey"
        )
        if not api_signal:
            return None
        return CodexCredential(
            auth_mode=AUTH_MODE_APIKEY,
            api_key=api_key,
            base_url=_str_or_none(meta("base_url")),
            email=_str_or_none(meta("email")),
            label=_str_or_none(meta("label")),
            source_format=source_format or _guess_format(obj, None),
            source_path=source_path,
            source_id=source_id or _str_or_none(meta("id")),
        )

    last_refresh = None
    for source in meta_sources:
        for key in ("last_refresh", "lastRefresh", "token_updated_at", "refreshed_at"):
            last_refresh = parse_timestamp(source.get(key))
            if last_refresh:
                break
        if last_refresh:
            break

    return CodexCredential(
        auth_mode=AUTH_MODE_CHATGPT,
        id_token=_str_or_none(_pick(block, "id_token")),
        access_token=_str_or_none(_pick(block, "access_token")),
        refresh_token=_str_or_none(_pick(block, "refresh_token")),
        account_id=_str_or_none(meta("account_id")),
        last_refresh=last_refresh,
        email=_str_or_none(meta("email")),
        plan_type=_str_or_none(meta("plan_type")),
        label=_str_or_none(meta("label")),
        source_format=source_format or _guess_format(obj, container_key),
        source_path=source_path,
        source_id=source_id or _str_or_none(meta("id")),
        requires_reauth=_bool(obj.get("requires_reauth") or obj.get("requiresReauth")),
        reauth_reason=_str_or_none(obj.get("reauth_reason") or obj.get("reauthReason")),
    )


def _cockpit_codex_entries(bundle: dict[str, Any]) -> list[Any] | None:
    schema = str(bundle.get("schema") or "")
    if not schema.startswith("cockpit-tools."):
        return None
    node: Any = bundle
    if isinstance(node.get("accounts"), dict) and "platforms" in node["accounts"]:
        node = node["accounts"]
    platforms = node.get("platforms")
    if not isinstance(platforms, dict):
        return []
    codex = platforms.get("codex")
    if isinstance(codex, dict):
        data = codex.get("exported_data") or codex.get("accounts") or []
        return data if isinstance(data, list) else []
    if isinstance(codex, list):
        return codex
    return []


def extract_codex_credentials(
    data: Any,
    *,
    source_path: str | None = None,
) -> list[CodexCredential]:
    """Return every Codex credential found in an already-parsed JSON document."""

    found: list[CodexCredential] = []

    def walk(node: Any, depth: int, forced_format: str | None) -> None:
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1, forced_format)
            return
        if not isinstance(node, dict):
            return

        cockpit = _cockpit_codex_entries(node)
        if cockpit is not None:
            for item in cockpit:
                walk(item, depth + 1, FORMAT_COCKPIT_EXPORT)
            return

        credential = credential_from_object(
            node,
            source_path=source_path,
            source_format=forced_format,
        )
        if credential is not None:
            found.append(credential)
            return

        for key in _CONTAINER_KEYS:
            child = node.get(key)
            if isinstance(child, list):
                walk(child, depth + 1, forced_format)
            elif isinstance(child, dict):
                # A single nested account, a map of id -> account, or another
                # container level.
                nested = credential_from_object(
                    child, source_path=source_path, source_format=forced_format
                )
                if nested is not None:
                    found.append(nested)
                    continue
                values = list(child.values())
                if values and all(isinstance(v, dict) for v in values):
                    walk(values, depth + 1, forced_format)
                else:
                    walk(child, depth + 1, forced_format)

    walk(data, 0, None)

    for index, credential in enumerate(found, start=1):
        if credential.source_id is None:
            credential.source_id = str(index)
    return found


def looks_like_codex_credentials(data: Any) -> bool:
    """Cheap check used by config auto-detection."""

    try:
        return bool(extract_codex_credentials(data))
    except Exception:  # pragma: no cover - defensive
        return False


def _parse_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        documents = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError:
                raise CredentialFormatError(
                    "File is neither JSON nor JSON Lines"
                ) from None
        if not documents:
            raise CredentialFormatError("File is empty") from None
        return documents


def load_codex_credentials(source: str | Path) -> list[CodexCredential]:
    """Load Codex credentials from a file, a HOME/CODEX_HOME dir, or Cockpit.

    *source* may be:

    * a credential file in any supported format;
    * ``cockpit`` / ``cockpit:<dir>`` or a Cockpit Tools data directory —
      read the live (encrypted) Cockpit account store;
    * a HOME directory containing ``.codex/auth.json`` or a ``CODEX_HOME``
      directory containing ``auth.json``.
    """

    from clippet.auth import cockpit

    text_source = str(source)
    if text_source == "cockpit" or text_source.startswith("cockpit:"):
        explicit = text_source.partition(":")[2] or None
        return cockpit.load_store_credentials(explicit)

    path = Path(text_source).expanduser()
    if path.is_dir():
        if cockpit.is_cockpit_dir(path):
            return cockpit.load_store_credentials(path)
        for candidate in (path / ".codex" / "auth.json", path / "auth.json"):
            if candidate.is_file():
                return load_codex_credentials(candidate)
        raise CredentialFormatError(
            f"No Codex credentials found in directory '{path}'. Expected "
            "'.codex/auth.json', 'auth.json', or a Cockpit Tools data directory."
        )

    if not path.is_file():
        raise FileNotFoundError(f"Credential source not found: {path}")

    data = _parse_text(path.read_text(encoding="utf-8"))
    credentials = extract_codex_credentials(data, source_path=str(path.resolve()))
    if not credentials:
        raise CredentialFormatError(
            f"No Codex credentials found in '{path}'. Supported formats: Codex "
            "auth.json, Cockpit Tools export/backup, sub2api export, CLIProxyAPI "
            "token file, or a JSON object with access_token/refresh_token."
        )
    return credentials


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def describe_choices(credentials: Iterable[CodexCredential]) -> str:
    lines = []
    for index, credential in enumerate(credentials, start=1):
        who = credential.display_email or credential.label or credential.display_account_id
        flag = " (needs re-login)" if credential.requires_reauth else ""
        lines.append(f"  [{index}] {who or '(unknown account)'} — {credential.auth_mode}{flag}")
    return "\n".join(lines)


def select_credential(
    credentials: list[CodexCredential],
    selector: str | int | None = None,
) -> CodexCredential:
    """Pick one credential.

    *selector* may be a 1-based index, an e-mail address (case-insensitive),
    the local part of an e-mail, a label, an account id or source id (prefix
    match allowed when unambiguous).  Without a selector the source must hold
    exactly one credential — or exactly one that does not need re-login.
    """

    if not credentials:
        raise CredentialSelectionError("No Codex credentials to choose from.")

    if selector is None or (isinstance(selector, str) and not selector.strip()):
        if len(credentials) == 1:
            return credentials[0]
        usable = [c for c in credentials if not c.requires_reauth]
        if len(usable) == 1:
            return usable[0]
        raise CredentialSelectionError(
            "The source holds several Codex accounts; choose one with --account:\n"
            + describe_choices(credentials)
        )

    text = str(selector).strip()
    if text.isdigit():
        index = int(text)
        if 1 <= index <= len(credentials):
            return credentials[index - 1]
        raise CredentialSelectionError(
            f"Account index {index} is out of range (1-{len(credentials)}):\n"
            + describe_choices(credentials)
        )

    lowered = text.lower()

    def exact_keys(credential: CodexCredential) -> set[str]:
        keys = set()
        email = credential.display_email
        if email:
            keys.add(email.lower())
            keys.add(email.lower().partition("@")[0])
        for value in (credential.label, credential.display_account_id, credential.source_id):
            if value:
                keys.add(str(value).lower())
        return keys

    matches = [c for c in credentials if lowered in exact_keys(c)]
    if not matches:
        matches = [
            c
            for c in credentials
            if any(
                str(value).lower().startswith(lowered)
                for value in (c.display_account_id, c.source_id, c.display_email)
                if value
            )
        ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CredentialSelectionError(
            f"No Codex account matches '{text}'. Available:\n" + describe_choices(credentials)
        )
    raise CredentialSelectionError(
        f"'{text}' matches several accounts; be more specific:\n" + describe_choices(matches)
    )


def load_codex_credential(
    source: str | Path,
    selector: str | int | None = None,
) -> CodexCredential:
    """Load *source* and select a single credential from it."""

    return select_credential(load_codex_credentials(source), selector)

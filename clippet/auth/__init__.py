"""Identity layer: agent credentials in their many storage formats.

See :mod:`clippet.auth.codex` for Codex credentials and
:mod:`clippet.auth.antigravity` for Cockpit Tools agy accounts.
"""

from clippet.auth.codex import (
    AUTH_MODE_APIKEY,
    AUTH_MODE_CHATGPT,
    CodexCredential,
    CredentialFormatError,
    CredentialSelectionError,
    extract_codex_credentials,
    load_codex_credential,
    load_codex_credentials,
    select_credential,
)

__all__ = [
    "AUTH_MODE_APIKEY",
    "AUTH_MODE_CHATGPT",
    "CodexCredential",
    "CredentialFormatError",
    "CredentialSelectionError",
    "extract_codex_credentials",
    "load_codex_credential",
    "load_codex_credentials",
    "select_credential",
]

"""Identity layer: agent credentials in their many storage formats.

Currently covers Codex (OpenAI) credentials — see :mod:`clippet.auth.codex`
for the supported formats and :mod:`clippet.auth.cockpit` for reading the
Cockpit Tools account store.
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

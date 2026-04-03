"""Isolated runtime environments for adapter subprocesses."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Protocol


class IsolatedEnvironment:
    """Create a sandboxed HOME directory and environment for one agent run."""

    def __init__(
        self,
        base_dir: Path | None = None,
        persist: bool = False,
        inherit_env: bool = True,
        env_overrides: dict[str, str] | None = None,
        env_whitelist: list[str] | None = None,
        env_blacklist: list[str] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser() if base_dir else None
        self._persist = persist
        self._inherit_env = inherit_env
        self._env_overrides = dict(env_overrides or {})
        self._env_whitelist = list(env_whitelist) if env_whitelist is not None else None
        self._env_blacklist = list(env_blacklist) if env_blacklist is not None else None
        self._home_dir: Path | None = None
        self._env: dict[str, str] | None = None

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

    def __enter__(self) -> IsolatedEnvironment:
        """Create the sandbox directory and environment."""

        self._home_dir = self._create_home_dir()
        self._env = self._build_env()
        return self

    def __exit__(self, *exc: object) -> None:
        """Tear down the sandbox unless persistence is enabled."""

        self.cleanup()

    def cleanup(self) -> None:
        """Delete the sandbox directory when persistence is disabled."""

        if self._home_dir is None:
            return

        cleanup_target = self._home_dir
        self._home_dir = None
        self._env = None

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
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o600)


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

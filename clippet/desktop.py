"""The Codex desktop app, one instance per env, side by side (macOS).

The app (bundle ``com.openai.codex``, installed as Codex.app or ChatGPT.app)
keeps two sets of state: Electron's (``userData``, which the app takes from
``CODEX_ELECTRON_USER_DATA_PATH``, and Chromium from ``--user-data-dir``)
and Codex's (``CODEX_HOME``: config.toml, the login, sessions).  Started by
``open -n`` with both pointing into an env, it runs as that env's account
next to the app you start from the Dock and to other envs' instances.  This
is how the app starts separate instances of itself, not a documented
interface: see docs/codex-desktop-multi-account-research.md.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BUNDLE_ID = "com.openai.codex"
#: OpenAI's signing team, which ``codex app`` also checks the app against.
TEAM_ID = "2DC432GLL2"
#: Where the app is, when CLIppet should not look for it.
APP_ENV = "CLIPPET_CODEX_APP"
#: Set to 1 to open an app that fails the signature check anyway.
UNVERIFIED_ENV = "CLIPPET_CODEX_APP_UNVERIFIED"
#: Electron's state for an env's instance, next to the env's .codex.
USER_DATA = ".codex-desktop"

_CANDIDATES = (
    "/Applications/Codex.app",
    "/Applications/ChatGPT.app",
    "~/Applications/Codex.app",
    "~/Applications/ChatGPT.app",
)


class DesktopError(RuntimeError):
    """The app cannot be opened as the env (said to people as it is)."""


@dataclass
class Instance:
    """Where one env's instance of the app keeps what."""

    home: Path

    @property
    def codex_home(self) -> Path:
        return self.home / ".codex"

    @property
    def user_data(self) -> Path:
        return self.home / USER_DATA

    @property
    def flag(self) -> str:
        return f"--user-data-dir={self.user_data}"


@dataclass
class Opened:
    """What :func:`open_app` did."""

    app: Path
    command: list[str]
    running: list[int] = field(default_factory=list)  # the env's instance was already open: its processes
    notes: list[str] = field(default_factory=list)


def bundle_id(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    value = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    return value if isinstance(value, str) else None


def find_app(environ: dict[str, str] | None = None) -> Path:
    """Where the Codex app is installed."""

    environ = os.environ if environ is None else environ
    override = environ.get(APP_ENV)
    if override:
        app = Path(override).expanduser()
        if bundle_id(app) != BUNDLE_ID:
            raise DesktopError(f"{APP_ENV}={override} is not the Codex app ({BUNDLE_ID})")
        return app
    for candidate in _CANDIDATES:
        app = Path(candidate).expanduser()
        if bundle_id(app) == BUNDLE_ID:
            return app
    try:
        found = subprocess.run(
            ["/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{BUNDLE_ID}'"],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        found = ""
    for line in found.splitlines():
        app = Path(line.strip())
        if app.suffix == ".app" and bundle_id(app) == BUNDLE_ID:
            return app
    raise DesktopError(
        "the Codex app is not installed: no app with bundle id com.openai.codex in /Applications or "
        f"Spotlight; install it (codex app does), or set {APP_ENV} to where it is"
    )


def signature_problem(app: Path) -> str | None:
    """Why *app* does not pass as OpenAI's signed app, or None when it does."""

    try:
        check = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                               capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"codesign could not check it ({exc})"
    if check.returncode != 0:
        lines = [line.strip() for line in check.stderr.splitlines() if line.strip()]
        return lines[0] if lines else "its signature is not valid"
    try:
        info = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(app)],
                              capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"codesign could not read its signer ({exc})"
    team = next((line.split("=", 1)[1].strip() for line in info.stderr.splitlines()
                 if line.startswith("TeamIdentifier=")), None)
    if team != TEAM_ID:
        return f"it is signed by team {team or '(none)'}, not by OpenAI ({TEAM_ID})"
    return None


def running(instance: Instance) -> list[int]:
    """The processes of the app running as *instance* (empty when it is not open)."""

    try:
        listing = subprocess.run(["/bin/ps", "-axww", "-o", "pid=,command="],
                                 capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        at = command.find(instance.flag)
        end = at + len(instance.flag)
        if at >= 0 and (end == len(command) or command[end] == " ") and pid.isdigit():
            pids.append(int(pid))
    return pids


def is_open(home: Path | str) -> bool:
    """Whether the Codex app is open as the env living in *home* (macOS; False elsewhere)."""

    if sys.platform != "darwin":
        return False
    return bool(running(Instance(Path(home).expanduser().resolve())))


def prepare(instance: Instance) -> list[str]:
    """Make *instance*'s folders, and keep its login in the env's auth.json; returns what changed."""

    from clippet.endpoints import read_codex_config, set_codex_config

    notes: list[str] = []
    for folder in (instance.codex_home, instance.user_data):
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
    try:
        config = read_codex_config(instance.home)
        if config.get("cli_auth_credentials_store") != "file":
            # The app does not get CLIppet's -c override: written down, a
            # login made in this instance lands in the env's auth.json
            set_codex_config(instance.home, "cli_auth_credentials_store", '"file"')
            notes.append("set cli_auth_credentials_store = \"file\" in the env's config.toml")
    except ValueError as exc:
        raise DesktopError(str(exc)) from exc
    return notes


def app_env(home: Path, environ: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Variables the env sets for Codex that the app should get, and what it gets from elsewhere.

    The app reads your login shell's variables itself, so a variable kept
    as a reference to one of the same name (``RELAY_KEY=${RELAY_KEY}``) is
    left to the shell rather than put on ``open``'s command line.
    """

    from clippet.config.environments import env_vars, profile_for_home
    from clippet.endpoints import resolve_vars

    found = profile_for_home(home)
    if found is None:
        return {}, []
    name, profile = found
    stored = env_vars(profile, "codex")
    passed = {key: value for key, value in stored.items() if value != "${" + key + "}"}
    notes = [f"the app reads ${key} from your login shell" for key in stored if key not in passed]
    return resolve_vars(passed, environ, where=f"env '{name}' (codex)"), notes


def open_command(app: Path, instance: Instance, paths: list[Path], extra: dict[str, str]) -> list[str]:
    """``open -n`` starting *app* as *instance* (the app's own way to start a separate instance)."""

    command = ["/usr/bin/open", "-n"]
    variables = {
        "CODEX_HOME": str(instance.codex_home),
        "CODEX_ELECTRON_USER_DATA_PATH": str(instance.user_data),
        **extra,
    }
    for key, value in variables.items():
        command += ["--env", f"{key}={value}"]
    command += ["-a", str(app), *(str(path) for path in paths), "--args", instance.flag]
    return command


def activate(pid: int) -> bool:
    """Bring the app process *pid* to the front (no Automation permission needed)."""

    script = (
        "ObjC.import('AppKit');"
        f"var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier({int(pid)});"
        "app && app.activateWithOptions($.NSApplicationActivateAllWindows | $.NSApplicationActivateIgnoringOtherApps);"
    )
    try:
        done = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                              capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


def open_app(
    home: Path | str,
    paths: list[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    before_launch=None,
    dry_run: bool = False,
) -> Opened:
    """Open the Codex app as the env living in *home*, next to any other instance.

    An instance of the env that is already open is brought to the front
    instead: a second process on the same folders could clash, and its login
    is left alone while it runs.  *before_launch* runs only when a new
    instance starts (e.g. pulling a linked env's newest login).
    """

    if sys.platform != "darwin":
        raise DesktopError("opening the Codex app as an env works on macOS only for now")
    environ = os.environ if environ is None else environ
    instance = Instance(Path(home).expanduser().resolve())
    if not instance.home.is_dir():
        raise DesktopError(f"the env's HOME is missing: {instance.home}")
    app = find_app(environ)
    targets = [Path(path).expanduser().resolve() for path in (paths or ["."])]
    for target in targets:
        if not target.exists():
            raise DesktopError(f"no such folder: {target}")

    pids = running(instance)
    if pids:
        opened = Opened(app=app, command=[], running=pids)
        if not dry_run and not activate(pids[0]):
            opened.notes.append("could not bring it to the front; switch to it from the Dock")
        opened.notes.append("it is already open as this env (open other folders from its window)")
        return opened

    problem = signature_problem(app)
    notes: list[str] = []
    if problem is not None:
        if environ.get(UNVERIFIED_ENV) != "1":
            raise DesktopError(
                f"{app} does not pass OpenAI's signature check: {problem}. `codex app` refuses it too; "
                f"reinstall the app from OpenAI, or set {UNVERIFIED_ENV}=1 to open it anyway"
            )
        notes.append(f"opened although it does not pass OpenAI's signature check: {problem}")
    extra, shell_notes = app_env(instance.home, environ)
    notes += shell_notes
    command = open_command(app, instance, targets, extra)
    if dry_run:
        return Opened(app=app, command=command, notes=notes)
    notes = prepare(instance) + notes
    if before_launch is not None:
        notes += list(before_launch() or [])
    done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if done.returncode != 0:
        raise DesktopError(f"open failed: {(done.stderr or done.stdout).strip() or done.returncode}")
    return Opened(app=app, command=command, notes=notes)

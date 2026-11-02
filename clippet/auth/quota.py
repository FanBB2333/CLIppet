"""How much of a Codex account's usage limits is left.

CLIppet does not ask OpenAI for this.  It reads the last reading that
something else took:

* Codex records one in its session logs after every turn: the
  ``rate_limits`` of a ``token_count`` event under ``<CODEX_HOME>/sessions``.
* Cockpit Tools keeps the answer of its own usage checks in each account
  record: ``quota`` (with the usage service's ``raw_data``) and
  ``usage_updated_at``.

A reading lists the account's limit windows (a 5-hour one, a weekly one,
...): how much of each is used and when it starts over.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCE_CODEX = "codex"
SOURCE_COCKPIT = "cockpit"
SOURCE_WORDS = {SOURCE_CODEX: "the last Codex run here", SOURCE_COCKPIT: "Cockpit Tools"}

_DAY = 24 * 60


def _time(value: Any) -> datetime | None:
    from clippet.auth.codex import parse_timestamp  # codex.py imports this module

    return parse_timestamp(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


@dataclass(frozen=True)
class QuotaWindow:
    """One limit window: how much of it is used, how long it is, when it starts over."""

    used_percent: float
    minutes: int | None = None
    resets_at: datetime | None = None

    def has_reset(self, now: datetime | None = None) -> bool:
        """True once the window has started over since the reading was taken."""

        return self.resets_at is not None and _now(now) >= self.resets_at

    def left(self, now: datetime | None = None) -> float:
        """The share still available, from 0 to 1; a window that has started over is full again."""

        if self.has_reset(now):
            return 1.0
        return min(1.0, max(0.0, 1.0 - self.used_percent / 100.0))

    @property
    def name(self) -> str:
        minutes = self.minutes
        if not minutes:
            return "limit"
        if minutes == 7 * _DAY:
            return "week"
        if minutes == _DAY:
            return "day"
        if minutes % _DAY == 0:
            return f"{minutes // _DAY}d"
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"


@dataclass(frozen=True)
class QuotaReading:
    """An account's limit windows as one source saw them at *taken_at*."""

    windows: tuple[QuotaWindow, ...]
    source: str
    taken_at: datetime | None = None
    plan: str | None = None

    def left(self, now: datetime | None = None) -> float:
        """The share left in the tightest window."""

        return min(window.left(now) for window in self.windows)


def newest(readings: Iterable[QuotaReading | None]) -> QuotaReading | None:
    """The reading taken last; one without a time loses to any that has one."""

    oldest = datetime.min.replace(tzinfo=timezone.utc)
    best: QuotaReading | None = None
    for reading in readings:
        if reading is None:
            continue
        if best is None or (reading.taken_at or oldest) > (best.taken_at or oldest):
            best = reading
    return best


def _window(used: Any, minutes: float | None, resets_at: datetime | None, after: Any,
            taken_at: datetime | None) -> QuotaWindow | None:
    used = _number(used)
    if used is None:
        return None
    after = _number(after)
    if resets_at is None and after is not None and taken_at is not None:
        resets_at = taken_at + timedelta(seconds=after)
    return QuotaWindow(used, int(minutes) if minutes else None, resets_at)


def _plan(data: dict[str, Any]) -> str | None:
    plan = data.get("plan_type")
    return plan if isinstance(plan, str) and plan else None


def reading_from_rate_limits(data: Any, taken_at: datetime | None = None) -> QuotaReading | None:
    """Codex's ``rate_limits``: ``primary`` and ``secondary`` windows with
    ``used_percent``, ``window_minutes`` and ``resets_at`` (``resets_in_seconds``
    in older versions)."""

    if not isinstance(data, dict):
        return None
    windows = []
    for key in ("primary", "secondary"):
        raw = data.get(key)
        if isinstance(raw, dict):
            window = _window(raw.get("used_percent"), _number(raw.get("window_minutes")),
                             _time(raw.get("resets_at")), raw.get("resets_in_seconds"), taken_at)
            if window is not None:
                windows.append(window)
    if not windows:
        return None
    return QuotaReading(tuple(windows), SOURCE_CODEX, taken_at, _plan(data))


def reading_from_usage(data: Any, taken_at: datetime | None = None,
                       source: str = SOURCE_COCKPIT) -> QuotaReading | None:
    """The usage service's answer: ``rate_limit.primary_window`` and
    ``secondary_window`` with ``used_percent``, ``limit_window_seconds`` and
    ``reset_at`` (or ``reset_after_seconds``)."""

    rate = data.get("rate_limit") if isinstance(data, dict) else None
    if not isinstance(rate, dict):
        return None
    windows = []
    for key in ("primary_window", "secondary_window"):
        raw = rate.get(key)
        if isinstance(raw, dict):
            seconds = _number(raw.get("limit_window_seconds"))
            window = _window(raw.get("used_percent"), seconds / 60 if seconds else None,
                             _time(raw.get("reset_at")), raw.get("reset_after_seconds"), taken_at)
            if window is not None:
                windows.append(window)
    if not windows:
        return None
    return QuotaReading(tuple(windows), source, taken_at, _plan(data))


def reading_from_cockpit(record: Any) -> QuotaReading | None:
    """The last usage check that Cockpit Tools stored in an account record."""

    if not isinstance(record, dict) or not isinstance(record.get("quota"), dict):
        return None
    quota = record["quota"]
    taken_at = _time(record.get("usage_updated_at"))
    reading = reading_from_usage(quota.get("raw_data"), taken_at)
    if reading is not None:
        return reading
    # Cockpit's own summary, in which *_percentage is the share left
    windows = []
    for prefix in ("hourly", "weekly"):
        if quota.get(f"{prefix}_window_present") is False:
            continue
        left = _number(quota.get(f"{prefix}_percentage"))
        if left is None:
            continue
        window = _window(100.0 - left, _number(quota.get(f"{prefix}_window_minutes")),
                         _time(quota.get(f"{prefix}_reset_time")), None, taken_at)
        if window is not None:
            windows.append(window)
    if not windows:
        return None
    return QuotaReading(tuple(windows), SOURCE_COCKPIT, taken_at, _plan(record))


def latest_codex_reading(codex_home: Path | str | None, *, logs: int = 8,
                         tail: int = 1 << 20) -> QuotaReading | None:
    """The newest ``rate_limits`` in the session logs under *codex_home*.

    Looks at the *logs* most recently written session files, and at most
    *tail* bytes from the end of each.
    """

    sessions = Path(codex_home) / "sessions" if codex_home else None
    if sessions is None or not sessions.is_dir():
        return None
    found: list[tuple[float, Path]] = []
    for path in sessions.rglob("rollout-*.jsonl"):
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(reverse=True)
    for _, path in found[:logs]:
        reading = _last_reading(path, tail)
        if reading is not None:
            return reading
    return None


def _last_reading(path: Path, tail: int) -> QuotaReading | None:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - tail))
            lines = handle.read().split(b"\n")
    except OSError:
        return None
    if size > tail:
        lines = lines[1:]  # the first line was cut
    for raw in reversed(lines):
        if b'"rate_limits"' not in raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        reading = reading_from_rate_limits(payload.get("rate_limits"), _time(event.get("timestamp")))
        if reading is not None:
            return reading
    return None


def codex_reading_for(codex_home: Path | str | None, plan: str | None = None) -> QuotaReading | None:
    """The newest session-log reading taken with the login now in *codex_home*.

    Account managers switch logins by rewriting ``auth.json``, so a reading
    taken before that file last changed may be another account's and is left
    out, as is one whose plan differs from *plan*.
    """

    reading = latest_codex_reading(codex_home)
    if reading is None or codex_home is None:
        return None
    try:
        changed = datetime.fromtimestamp((Path(codex_home) / "auth.json").stat().st_mtime, timezone.utc)
    except OSError:
        changed = None
    if changed is not None and (reading.taken_at is None or reading.taken_at < changed):
        return None
    if plan and reading.plan and plan.lower() != reading.plan.lower():
        return None
    return reading

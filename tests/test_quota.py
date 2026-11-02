"""Reading how much of a Codex account's usage limits is left."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clippet.auth.codex import FORMAT_COCKPIT_EXPORT, credential_from_object
from clippet.auth.quota import (
    SOURCE_CODEX,
    SOURCE_COCKPIT,
    QuotaReading,
    QuotaWindow,
    codex_reading_for,
    latest_codex_reading,
    newest,
    reading_from_cockpit,
    reading_from_rate_limits,
)
from tests.codex_fixtures import cockpit_account, cockpit_quota

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def _stamp(moment: datetime) -> float:
    return moment.timestamp()


def _token_count(at: datetime, used: float | None, plan: str = "plus") -> dict:
    limits = None if used is None else {
        "primary": {"used_percent": used, "window_minutes": 10080, "resets_at": int(_stamp(at + timedelta(days=2)))},
        "secondary": None,
        "plan_type": plan,
    }
    return {"timestamp": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "type": "event_msg",
            "payload": {"type": "token_count", "info": None, "rate_limits": limits}}


def _rollout(folder: Path, name: str, events: list[dict], written: datetime) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    os.utime(path, (_stamp(written), _stamp(written)))
    return path


def test_codex_rate_limits_become_windows() -> None:
    reading = reading_from_rate_limits({
        "primary": {"used_percent": 25.0, "window_minutes": 300, "resets_at": int(_stamp(NOW + timedelta(hours=2)))},
        "secondary": {"used_percent": 69.0, "window_minutes": 10080, "resets_in_seconds": 3 * 86400},
        "plan_type": "plus",
    }, taken_at=NOW)
    assert reading.source == SOURCE_CODEX and reading.plan == "plus"
    five, week = reading.windows
    assert (five.name, week.name) == ("5h", "week")
    assert five.left(NOW) == 0.75 and round(week.left(NOW), 2) == 0.31
    assert week.resets_at == NOW + timedelta(days=3)  # older Codex: seconds from the reading
    assert round(reading.left(NOW), 2) == 0.31  # the tightest window
    assert reading_from_rate_limits(None) is None and reading_from_rate_limits({"primary": None}) is None


def test_a_window_that_has_started_over_is_full_again() -> None:
    assert QuotaWindow(100.0, 10080, NOW + timedelta(minutes=1)).left(NOW) == 0.0
    window = QuotaWindow(100.0, 10080, NOW - timedelta(minutes=1))
    assert window.has_reset(NOW) and window.left(NOW) == 1.0


def test_cockpit_keeps_the_usage_services_answer() -> None:
    checked = int(_stamp(NOW))
    record = cockpit_account("alice@example.com", "acct-alice", usage_updated_at=checked,
                             quota=cockpit_quota((0, 300, 7200), (51, 10080, 150_000), checked_at=checked))
    reading = reading_from_cockpit(record)
    assert reading.source == SOURCE_COCKPIT and reading.taken_at == NOW
    assert [window.name for window in reading.windows] == ["5h", "week"]
    assert reading.windows[1].resets_at == NOW + timedelta(seconds=150_000)
    assert round(reading.left(NOW), 2) == 0.49


def test_cockpits_own_summary_counts_the_share_left() -> None:
    record = {
        "quota": {"hourly_percentage": 31, "hourly_window_minutes": 10080, "hourly_window_present": True,
                  "hourly_reset_time": int(_stamp(NOW + timedelta(days=3))),
                  "weekly_percentage": 100, "weekly_window_present": False},
        "usage_updated_at": int(_stamp(NOW)),
    }
    reading = reading_from_cockpit(record)
    assert [(w.name, w.used_percent) for w in reading.windows] == [("week", 69.0)]
    assert reading_from_cockpit({"quota": None, "quota_error": {"code": "invalid_refresh_token"}}) is None


def test_credentials_from_cockpit_carry_their_reading() -> None:
    checked = int(_stamp(NOW))
    record = cockpit_account("alice@example.com", "acct-alice", usage_updated_at=checked,
                             quota=cockpit_quota((40, 300, 3600), checked_at=checked))
    credential = credential_from_object(record, source_format=FORMAT_COCKPIT_EXPORT)
    assert credential.quota is not None and credential.quota.windows[0].used_percent == 40
    written = json.dumps(credential.to_auth_json())
    assert "quota" not in written and "used_percent" not in written  # the reading never reaches auth.json


def test_the_newest_session_log_reading_wins(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    day = codex_home / "sessions" / "2026" / "11" / "02"
    _rollout(day, "old", [_token_count(NOW - timedelta(hours=5), 10.0)], NOW - timedelta(hours=1))
    _rollout(day, "new", [
        {"type": "session_meta", "payload": {"id": "x"}},
        _token_count(NOW - timedelta(minutes=30), 40.0),
        _token_count(NOW - timedelta(minutes=10), 45.0),
        _token_count(NOW - timedelta(minutes=5), None),  # turns without limits (API keys) are skipped
    ], NOW)
    reading = latest_codex_reading(codex_home)
    assert reading.windows[0].used_percent == 45.0
    assert reading.taken_at == NOW - timedelta(minutes=10)
    assert latest_codex_reading(tmp_path / "nowhere") is None


def test_readings_taken_before_the_login_changed_are_left_out(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    _rollout(codex_home / "sessions", "a", [_token_count(NOW - timedelta(minutes=10), 45.0)], NOW)
    auth = codex_home / "auth.json"
    auth.write_text("{}")
    os.utime(auth, (_stamp(NOW - timedelta(hours=1)),) * 2)
    assert codex_reading_for(codex_home, "plus").windows[0].used_percent == 45.0
    assert codex_reading_for(codex_home, "pro") is None  # another plan: another account
    os.utime(auth, (_stamp(NOW - timedelta(minutes=1)),) * 2)
    assert codex_reading_for(codex_home, "plus") is None  # the login was switched after the reading


def test_newest_prefers_the_latest_reading() -> None:
    windows = (QuotaWindow(10.0),)
    old = QuotaReading(windows, SOURCE_COCKPIT, NOW - timedelta(hours=1))
    new = QuotaReading(windows, SOURCE_CODEX, NOW)
    undated = QuotaReading(windows, SOURCE_COCKPIT)
    assert newest([old, None, new, undated]) is new
    assert newest([undated, None]) is undated and newest([]) is None

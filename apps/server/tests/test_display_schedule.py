from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from core.services.display_schedule import annotate_screen, display_availability

SAKHALIN = ZoneInfo("Asia/Sakhalin")


def screen(schedule=None, *, zone="Asia/Sakhalin"):
    return SimpleNamespace(
        settings={} if schedule is None else {"displaySchedule": schedule},
        channel=SimpleNamespace(timezone_name=zone),
        last_seen_at=None,
        capabilities={},
        status="online",
        Status=SimpleNamespace(OFFLINE="offline"),
    )


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=SAKHALIN)


def interval(**values):
    return {
        "mode": "interval", "intervalMinutes": 60, "durationMinutes": 10,
        "dailyStart": "09:00", "dailyEnd": "18:00", "weekdays": [], **values,
    }


@pytest.mark.parametrize(
    ("at", "active", "until", "next_start"),
    [
        ("2026-10-19T08:59:59", False, None, "2026-10-19T09:00:00"),
        ("2026-10-19T09:00:00", True, "2026-10-19T09:10:00", "2026-10-19T10:00:00"),
        ("2026-10-19T09:09:59", True, "2026-10-19T09:10:00", "2026-10-19T10:00:00"),
        ("2026-10-19T09:10:00", False, None, "2026-10-19T10:00:00"),
        ("2026-10-19T10:00:00", True, "2026-10-19T10:10:00", "2026-10-19T11:00:00"),
        ("2026-10-19T17:10:00", False, None, "2026-10-20T09:00:00"),
        ("2026-10-19T18:00:00", False, None, "2026-10-20T09:00:00"),
    ],
)
def test_hourly_sessions_have_exact_inclusive_start_exclusive_end(at, active, until, next_start):
    availability = display_availability(screen(interval()), local(at))
    assert availability["expectedActive"] is active
    assert availability["activeUntil"] == (local(until) if until else None)
    assert availability["nextStart"] == local(next_start)


def test_interval_120_minutes_starts_from_beginning_of_workday():
    target = screen(interval(intervalMinutes=120, durationMinutes=15))
    assert display_availability(target, local("2026-10-19T10:00:00"))["nextStart"] == local("2026-10-19T11:00:00")
    active = display_availability(target, local("2026-10-19T11:00:00"))
    assert active["expectedActive"]
    assert active["activeUntil"] == local("2026-10-19T11:15:00")


def test_session_is_truncated_at_end_of_working_time():
    target = screen(interval(dailyEnd="09:05"))
    active = display_availability(target, local("2026-10-19T09:04:00"))
    assert active["activeUntil"] == local("2026-10-19T09:05:00")
    assert not display_availability(target, local("2026-10-19T09:05:00"))["expectedActive"]


@pytest.mark.parametrize(
    ("at", "active", "next_start"),
    [
        ("2026-10-19T08:59:59", False, "2026-10-19T09:00:00"),
        ("2026-10-19T09:00:00", True, "2026-10-19T12:00:00"),
        ("2026-10-19T09:10:00", False, "2026-10-19T12:00:00"),
        ("2026-10-19T12:00:00", True, "2026-10-20T09:00:00"),
        ("2026-10-19T12:15:00", False, "2026-10-20T09:00:00"),
    ],
)
def test_fixed_windows(at, active, next_start):
    target = screen({"mode": "windows", "windows": [
        {"start": "09:00", "end": "09:10"}, {"start": "12:00", "end": "12:15"},
    ]})
    result = display_availability(target, local(at))
    assert result["expectedActive"] is active
    assert result["nextStart"] == local(next_start)


@pytest.mark.parametrize("mode", ["interval", "windows"])
def test_overnight_weekday_is_owned_by_the_day_it_started(mode):
    config = interval(dailyStart="22:00", dailyEnd="06:00", weekdays=[0])
    if mode == "windows":
        config = {"mode": "windows", "weekdays": [0], "windows": [{"start": "22:00", "end": "06:00"}]}
    result = display_availability(screen(config), local("2026-10-20T01:00:00"))
    assert result["expectedActive"]
    assert result["activeUntil"] == local(
        "2026-10-20T01:10:00" if mode == "interval" else "2026-10-20T06:00:00"
    )
    ended = display_availability(screen(config), local("2026-10-20T06:00:00"))
    assert not ended["expectedActive"]
    assert ended["nextStart"] == local("2026-10-26T22:00:00")


def test_invalid_timezone_uses_sakhalin_fallback():
    result = display_availability(screen(interval(), zone="Not/A_Timezone"), datetime.fromisoformat("2026-10-18T22:00:00+00:00"))
    assert result["expectedActive"]
    assert result["activeUntil"] == local("2026-10-19T09:10:00")


@pytest.mark.parametrize("settings", [None, [], "invalid", {"displaySchedule": None}, {"displaySchedule": []}, {"displaySchedule": "invalid"}, {"displaySchedule": {"mode": []}}])
def test_malformed_legacy_settings_do_not_break_overview(settings):
    target = screen()
    target.settings = settings
    assert display_availability(target, local("2026-10-19T10:00:00"))["expectedActive"]


@pytest.mark.parametrize("schedule", [
    {"mode": "interval", "dailyStart": "bad"},
    {"mode": "interval", "intervalMinutes": "bad"},
    {"mode": "interval", "intervalMinutes": 10**100},
    {"mode": "windows", "windows": [None]},
    {"mode": "windows", "windows": [{"start": "25:00", "end": "12:00"}]},
])
def test_bad_schedule_values_fall_back_safely(schedule):
    assert display_availability(screen(schedule), local("2026-10-19T10:00:00"))["expectedActive"]


def test_stale_presence_becomes_offline_and_pause_remains_separate():
    target = screen(interval())
    target.last_seen_at = local("2026-10-19T09:00:00")
    annotated = annotate_screen(target, local("2026-10-19T09:30:00"))
    assert annotated.status == "offline"
    assert annotated.expected_active is False
    assert annotated.next_start == local("2026-10-19T10:00:00")

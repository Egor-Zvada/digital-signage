from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone


def display_availability(screen, at=None) -> dict:
    """Expected browser presence only. The Pi's existing program controls its window."""
    settings = screen.settings if isinstance(screen.settings, dict) else {}
    schedule = settings.get("displaySchedule", {})
    schedule = schedule if isinstance(schedule, dict) else {}
    mode = schedule.get("mode", "always")
    result = {"expectedActive": True, "nextStart": None, "activeUntil": None, "label": "При каждом открытии браузера"}
    if not isinstance(mode, str) or mode not in {"interval", "windows"}:
        return result
    try:
        zone = ZoneInfo(screen.channel.timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = ZoneInfo("Asia/Sakhalin")
    current = (at or timezone.now()).astimezone(zone)
    try:
        weekdays = schedule.get("weekdays") or list(range(7))
        ranges = []
        for offset in range(-1, 9):
            day = current.date() + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            if mode == "interval":
                start_time = time.fromisoformat(schedule.get("dailyStart", "09:00"))
                end_time = time.fromisoformat(schedule.get("dailyEnd", "18:00"))
                start = datetime.combine(day, start_time, zone)
                end = datetime.combine(day, end_time, zone)
                if end <= start:
                    end += timedelta(days=1)
                minutes = int(schedule.get("intervalMinutes", 60))
                duration_minutes = int(schedule.get("durationMinutes", 10))
                if not 5 <= minutes <= 1440 or not 1 <= duration_minutes <= minutes:
                    raise ValueError("Invalid interval")
                interval = timedelta(minutes=minutes)
                duration = timedelta(minutes=duration_minutes)
                while start < end and len(ranges) < 15000:
                    ranges.append((start, min(start + duration, end)))
                    start += interval
            else:
                for window in schedule.get("windows", [])[:32]:
                    start = datetime.combine(day, time.fromisoformat(window["start"]), zone)
                    end = datetime.combine(day, time.fromisoformat(window["end"]), zone)
                    if end <= start:
                        end += timedelta(days=1)
                    ranges.append((start, end))
        ranges.sort()
        active = [(start, end) for start, end in ranges if start <= current < end]
        future = next((start for start, _ in ranges if start > current), None)
        result.update(
            expectedActive=bool(active),
            nextStart=future,
            activeUntil=max((end for _, end in active), default=None),
            label=(f"Каждые {int(schedule.get('intervalMinutes', 60))} мин. на {int(schedule.get('durationMinutes', 10))} мин."
                   if mode == "interval" else "По заданным часам"),
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        # Older settings edited outside the panel must not break the overview.
        pass
    return result


def annotate_screen(screen, at=None):
    current = at or timezone.now()
    availability = display_availability(screen, current)
    screen.expected_active = availability["expectedActive"]
    screen.schedule_label = availability["label"]
    screen.next_start = availability["nextStart"]
    caps = screen.capabilities if isinstance(screen.capabilities, dict) else {}
    screen.session_closed = bool(caps.get("sessionClosedAt"))
    if not screen.last_seen_at or current - screen.last_seen_at > timedelta(seconds=90):
        screen.status = screen.Status.OFFLINE
    screen.is_online = screen.status != screen.Status.OFFLINE and bool(screen.last_seen_at)
    return screen

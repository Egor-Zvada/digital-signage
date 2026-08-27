from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def is_schedule_active(
    *,
    now: datetime,
    timezone_name: str,
    active_from: datetime | None,
    active_until: datetime | None,
    weekdays: list[int] | None,
    daily_start,
    daily_end,
) -> bool:
    if active_from and now < active_from:
        return False
    if active_until and now >= active_until:
        return False

    local_now = now.astimezone(ZoneInfo(timezone_name))
    if weekdays and local_now.weekday() not in weekdays:
        return False
    if daily_start is None and daily_end is None:
        return True

    current = local_now.timetz().replace(tzinfo=None)
    if daily_start is not None and daily_end is not None and daily_end <= daily_start:
        return current >= daily_start or current < daily_end
    if daily_start is not None and current < daily_start:
        return False
    return not (daily_end is not None and current >= daily_end)

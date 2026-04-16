"""Pure slot-math: compute available appointment start times.

No I/O. Easy to unit-test. Inputs are timezone-aware datetimes; outputs are too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Tuple

from tools.config import BusinessConfig

GRID_MINUTES = 15
LOOKAHEAD_DAYS = 7


@dataclass(frozen=True)
class BusyInterval:
    start: datetime  # tz-aware
    end: datetime    # tz-aware


def _round_up(dt: datetime, minutes: int) -> datetime:
    """Round dt up to the next `minutes` boundary."""
    discard = timedelta(
        minutes=dt.minute % minutes,
        seconds=dt.second,
        microseconds=dt.microsecond,
    )
    if discard == timedelta(0):
        return dt
    return dt + (timedelta(minutes=minutes) - discard)


def _overlaps(a_start: datetime, a_end: datetime, busy: Iterable[BusyInterval]) -> bool:
    for b in busy:
        if a_start < b.end and b.start < a_end:
            return True
    return False


def compute_available_slots(
    duration_minutes: int,
    config: BusinessConfig,
    busy: Iterable[BusyInterval],
    now: datetime,
    *,
    lookahead_days: int = LOOKAHEAD_DAYS,
    grid_minutes: int = GRID_MINUTES,
    max_slots: int | None = None,
) -> List[datetime]:
    """Return tz-aware datetimes (in `now`'s tz) where an appointment of the given
    duration could start, within business hours, not overlapping any busy interval,
    over the next `lookahead_days`."""
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware")

    busy_list = list(busy)
    out: List[datetime] = []

    for day_offset in range(lookahead_days):
        day = (now + timedelta(days=day_offset)).date()
        hours = config.hours_for_weekday(day.weekday())
        if hours is None:
            continue

        day_open = datetime.combine(day, hours.open, tzinfo=now.tzinfo)
        day_close = datetime.combine(day, hours.close, tzinfo=now.tzinfo)

        cursor = max(day_open, _round_up(now, grid_minutes)) if day_offset == 0 else day_open
        cursor = _round_up(cursor, grid_minutes)
        last_start = day_close - timedelta(minutes=duration_minutes)

        while cursor <= last_start:
            slot_end = cursor + timedelta(minutes=duration_minutes)
            if not _overlaps(cursor, slot_end, busy_list):
                out.append(cursor)
                if max_slots is not None and len(out) >= max_slots:
                    return out
            cursor += timedelta(minutes=grid_minutes)

    return out

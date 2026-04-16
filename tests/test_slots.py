from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.config import load_business_config
from tools.slots import BusyInterval, compute_available_slots

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "business.yaml"
TZ = timezone.utc


def _monday_at(hour: int, minute: int = 0) -> datetime:
    # Pick a known Monday (2026-04-20 is a Monday).
    return datetime(2026, 4, 20, hour, minute, tzinfo=TZ)


def test_slots_in_business_hours_only():
    cfg = load_business_config(CONFIG_PATH)
    now = _monday_at(8, 30)  # before opening
    slots = compute_available_slots(30, cfg, busy=[], now=now, lookahead_days=1)
    assert slots, "expected at least one slot on a workday"
    for s in slots:
        assert s.weekday() in {0, 1, 2, 3, 4}
        # must start within 09:00..(17:00 - 30min)
        assert (s.hour, s.minute) >= (9, 0)
        assert s + timedelta(minutes=30) <= s.replace(hour=17, minute=0)


def test_skips_closed_days():
    cfg = load_business_config(CONFIG_PATH)
    saturday = datetime(2026, 4, 25, 8, 0, tzinfo=TZ)
    slots = compute_available_slots(30, cfg, busy=[], now=saturday, lookahead_days=2)
    # Sat + Sun closed; expect empty for a 2-day window.
    assert slots == []


def test_excludes_busy_intervals():
    cfg = load_business_config(CONFIG_PATH)
    now = _monday_at(8, 30)
    busy = [BusyInterval(_monday_at(10, 0), _monday_at(11, 0))]
    slots = compute_available_slots(30, cfg, busy=busy, now=now, lookahead_days=1)
    for s in slots:
        s_end = s + timedelta(minutes=30)
        assert not (s < busy[0].end and busy[0].start < s_end), f"slot {s} overlaps busy"
    # 09:00, 09:15, 09:30 should be present; 10:00, 10:15, 10:30 should not.
    starts = {(s.hour, s.minute) for s in slots if s.date() == _monday_at(9).date()}
    assert (9, 0) in starts
    assert (9, 30) in starts
    assert (10, 0) not in starts
    assert (10, 30) not in starts
    assert (11, 0) in starts


def test_respects_now_cursor():
    cfg = load_business_config(CONFIG_PATH)
    now = _monday_at(10, 7)  # mid-morning, mid-grid
    slots = compute_available_slots(30, cfg, busy=[], now=now, lookahead_days=1)
    today = [s for s in slots if s.date() == now.date()]
    assert today, "should still have same-day slots after 10:07"
    # First slot today must be at or after the next 15-min boundary (10:15).
    assert today[0] >= _monday_at(10, 15)


def test_max_slots_caps_output():
    cfg = load_business_config(CONFIG_PATH)
    now = _monday_at(8, 30)
    slots = compute_available_slots(30, cfg, busy=[], now=now, lookahead_days=7, max_slots=5)
    assert len(slots) == 5


def test_duration_must_fit_before_close():
    cfg = load_business_config(CONFIG_PATH)
    now = _monday_at(8, 30)
    slots = compute_available_slots(60, cfg, busy=[], now=now, lookahead_days=1)
    # last legal start for a 60-min slot is 16:00
    assert slots[-1].hour == 16 and slots[-1].minute == 0

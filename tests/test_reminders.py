"""Reminder engine tests against seeded appointments."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("OWNER_WHATSAPP", "+15559999999")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BUSINESS_CONFIG_PATH", str(ROOT / "config" / "business.yaml"))
    from tools import whatsapp, gcal
    monkeypatch.setattr(whatsapp, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(gcal, "STUB_FILE", tmp_path / "calendar.json")
    yield tmp_path


def _outbox_payloads(tmp_path):
    f = tmp_path / "outbox.jsonl"
    if not f.exists():
        return []
    return [json.loads(l)["payload"] for l in f.read_text().splitlines() if l.strip()]


@pytest.mark.asyncio
async def test_full_reminder_tick_fires_each_branch(env):
    from app import db
    from app.reminders import tick
    from scripts.seed_demo import seed
    from tools.config import load_business_config

    a1, a2, a3, a4 = await seed()
    cfg = load_business_config(ROOT / "config" / "business.yaml")
    await tick(cfg)

    payloads = _outbox_payloads(env)
    # Bucket payloads by recipient phone for easier assertions.
    by_to = {}
    for p in payloads:
        by_to.setdefault(p["to"], []).append(p)

    # a1 → 24h reminder with Confirm/Reschedule buttons
    msgs = by_to.get("+15550000001", [])
    assert any(m["type"] == "interactive" and m["interactive"]["type"] == "button" for m in msgs), \
        "expected 24h reminder buttons for a1"

    # a2 → 2h reminder text containing the address
    msgs = by_to.get("+15550000002", [])
    assert any(m["type"] == "text" and "Main St" in m["text"]["body"] for m in msgs), \
        "expected 2h reminder text for a2"

    # a3 → follow-up: a buttons message + a review-link text
    msgs = by_to.get("+15550000003", [])
    assert any(m["type"] == "interactive" and m["interactive"]["type"] == "button" for m in msgs), \
        "expected follow-up buttons for a3"
    assert any(m["type"] == "text" and "Review link" in m["text"]["body"] for m in msgs)

    # a4 → owner gets a no-show notice
    owner_msgs = by_to.get("+15559999999", [])
    assert any(m["type"] == "text" and "No-show" in m["text"]["body"] and "Darwin" in m["text"]["body"]
               for m in owner_msgs), "expected owner no-show notification for a4"

    # State transitions / flags
    a1_now = await db.get_appointment(a1)
    assert a1_now.reminder_24h_sent == 1 and a1_now.status == "booked"

    a2_now = await db.get_appointment(a2)
    assert a2_now.reminder_2h_sent == 1

    a3_now = await db.get_appointment(a3)
    assert a3_now.followup_sent == 1 and a3_now.status == "completed"

    a4_now = await db.get_appointment(a4)
    assert a4_now.status == "no_show"


@pytest.mark.asyncio
async def test_tick_is_idempotent(env):
    """Running tick twice in a row must NOT re-send anything."""
    from app.reminders import tick
    from scripts.seed_demo import seed
    from tools.config import load_business_config

    await seed()
    cfg = load_business_config(ROOT / "config" / "business.yaml")
    await tick(cfg)
    first_count = len(_outbox_payloads(env))

    await tick(cfg)
    second_count = len(_outbox_payloads(env))
    assert first_count == second_count, "second tick must not produce new outbound messages"


@pytest.mark.asyncio
async def test_no_followup_outside_window():
    """A 30-min appointment that ended 30 minutes ago is too recent
    for the follow-up window (45–75m after end), so it stays untouched."""
    from datetime import datetime, timedelta, timezone
    from app import db
    from app.reminders import tick
    from tools.config import load_business_config

    await db.migrate()
    now = datetime.now(timezone.utc)
    appt = await db.create_appointment(
        name="Tooearly", phone="+15550008888", service="Consultation",
        when=now - timedelta(minutes=60), duration_minutes=30,
        calendar_event_id="evt-too-early",
    )
    # ends at now-30m → outside follow-up window (which is now-75m..now-45m)
    await db.mark_flag(appt.id, "reminder_24h_sent")
    await db.mark_flag(appt.id, "reminder_2h_sent")

    cfg = load_business_config(ROOT / "config" / "business.yaml")
    await tick(cfg)

    fresh = await db.get_appointment(appt.id)
    assert fresh.followup_sent == 0  # ended too recently to qualify for follow-up

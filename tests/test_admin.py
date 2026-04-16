"""Owner admin commands: Today / Tomorrow / Stats."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
OWNER = "+15559999999"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("OWNER_WHATSAPP", OWNER)
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BUSINESS_CONFIG_PATH", str(ROOT / "config" / "business.yaml"))
    from tools import whatsapp, gcal
    monkeypatch.setattr(whatsapp, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(gcal, "STUB_FILE", tmp_path / "calendar.json")
    from app.main import app
    with TestClient(app) as c:
        c.outbox = tmp_path / "outbox.jsonl"
        yield c


def _text_from_owner(body):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{
            "value": {"messaging_product": "whatsapp",
                      "metadata": {"display_phone_number": "x", "phone_number_id": "y"},
                      "messages": [{"from": OWNER.lstrip("+"), "id": "wamid.x", "timestamp": "1",
                                    "type": "text", "text": {"body": body}}]},
            "field": "messages",
        }]}],
    }


def _last(client):
    return json.loads(client.outbox.read_text().splitlines()[-1])["payload"]


@pytest.mark.asyncio
async def _seed_appts():
    """Seed: 2 today, 1 tomorrow, plus historical week stats."""
    from app import db
    await db.migrate()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_morning = today_start + timedelta(hours=9)   # always belongs to today UTC
    tomorrow = today_start + timedelta(days=1, hours=9)

    await db.create_appointment(name="Alice", phone="+1", service="Consultation",
                                when=today_morning, duration_minutes=30, calendar_event_id="e1")
    await db.create_appointment(name="Bob", phone="+2", service="Treatment",
                                when=today_morning + timedelta(hours=1), duration_minutes=60,
                                calendar_event_id="e2")
    await db.create_appointment(name="Carol", phone="+3", service="Consultation",
                                when=tomorrow, duration_minutes=30, calendar_event_id="e3")

    # For stats: week of today contains 3 above + 1 completed + 1 no_show + 1 cancelled
    completed = await db.create_appointment(name="D", phone="+4", service="Consultation",
                                            when=now - timedelta(hours=2), duration_minutes=30,
                                            calendar_event_id="e4")
    await db.update_appointment_status(completed.id, "completed")
    no_show = await db.create_appointment(name="E", phone="+5", service="Consultation",
                                          when=now - timedelta(hours=3), duration_minutes=30,
                                          calendar_event_id="e5")
    await db.update_appointment_status(no_show.id, "no_show")
    cancelled = await db.create_appointment(name="F", phone="+6", service="Consultation",
                                            when=now - timedelta(hours=4), duration_minutes=30,
                                            calendar_event_id="e6")
    await db.update_appointment_status(cancelled.id, "cancelled")


def test_today_lists_today_appts(client):
    import asyncio
    asyncio.get_event_loop().run_until_complete(_seed_appts())
    r = client.post("/webhook", json=_text_from_owner("Today"))
    assert r.status_code == 200
    body = _last(client)["text"]["body"]
    assert "Alice" in body and "Bob" in body
    assert "Carol" not in body  # tomorrow's


def test_tomorrow_lists_tomorrow_appts(client):
    import asyncio
    asyncio.get_event_loop().run_until_complete(_seed_appts())
    r = client.post("/webhook", json=_text_from_owner("Tomorrow"))
    assert r.status_code == 200
    body = _last(client)["text"]["body"]
    assert "Carol" in body
    assert "Alice" not in body


def test_stats_reports_week_totals(client):
    import asyncio
    asyncio.get_event_loop().run_until_complete(_seed_appts())
    r = client.post("/webhook", json=_text_from_owner("Stats"))
    assert r.status_code == 200
    body = _last(client)["text"]["body"]
    # 6 appointments seeded total in this week: 3 future-booked, 1 completed, 1 no_show, 1 cancelled.
    assert "Bookings: 6" in body
    assert "Completed: 1" in body
    assert "No-shows: 1" in body
    assert "Cancelled: 1" in body
    # Completion rate = 1 / (1 + 1 + 1) ≈ 33%
    assert "33%" in body


def test_non_owner_does_not_get_admin(client):
    import asyncio
    asyncio.get_event_loop().run_until_complete(_seed_appts())
    # Same "Today" command from a different number → falls through to help text.
    payload = _text_from_owner("Today")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = "15550008888"
    r = client.post("/webhook", json=payload)
    assert r.status_code == 200
    body = _last(client)["text"]["body"]
    assert "Book" in body and "Alice" not in body

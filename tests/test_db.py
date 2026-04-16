import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch, tmp_path):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    yield


@pytest.mark.asyncio
async def test_migrate_then_full_appointment_lifecycle():
    from app import db
    await db.migrate()

    when = datetime.now(timezone.utc) + timedelta(days=1)
    appt = await db.create_appointment(
        name="Ada", phone="+15550000001", service="Consultation",
        when=when, duration_minutes=30, calendar_event_id="evt-123",
    )
    assert appt.status == "booked"

    fetched = await db.get_appointment(appt.id)
    assert fetched is not None and fetched.name == "Ada"

    await db.mark_flag(appt.id, "reminder_24h_sent")
    again = await db.get_appointment(appt.id)
    assert again.reminder_24h_sent == 1

    await db.update_appointment_status(appt.id, "confirmed")
    final = await db.get_appointment(appt.id)
    assert final.status == "confirmed"


@pytest.mark.asyncio
async def test_mark_flag_rejects_arbitrary_columns():
    from app import db
    await db.migrate()
    with pytest.raises(ValueError):
        await db.mark_flag("anything", "DROP TABLE appointments")


@pytest.mark.asyncio
async def test_users_upsert():
    from app import db
    await db.migrate()
    await db.upsert_user("+15550000002", "Grace")
    assert (await db.get_user("+15550000002"))["name"] == "Grace"
    await db.upsert_user("+15550000002", "Grace H.")
    assert (await db.get_user("+15550000002"))["name"] == "Grace H."
    assert await db.get_user("+15550009999") is None


@pytest.mark.asyncio
async def test_sessions_set_get_delete():
    from app import db
    await db.migrate()
    await db.set_session("+15550000003", "AWAITING_SLOT", {"service": "Treatment", "duration_minutes": 60})
    s = await db.get_session("+15550000003")
    assert s.state == "AWAITING_SLOT"
    assert s.data["service"] == "Treatment"

    # Update overwrites cleanly.
    await db.set_session("+15550000003", "AWAITING_NAME", {"slot": "2026-04-20T10:00Z"})
    s2 = await db.get_session("+15550000003")
    assert s2.state == "AWAITING_NAME" and "service" not in s2.data

    await db.delete_session("+15550000003")
    assert await db.get_session("+15550000003") is None


@pytest.mark.asyncio
async def test_query_appointments_filters():
    from app import db
    await db.migrate()
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    later = datetime.now(timezone.utc) + timedelta(hours=24)
    a1 = await db.create_appointment(
        name="A", phone="+1", service="Consultation", when=soon,
        duration_minutes=30, calendar_event_id=None,
    )
    a2 = await db.create_appointment(
        name="B", phone="+2", service="Consultation", when=later,
        duration_minutes=30, calendar_event_id=None,
    )
    booked = await db.query_appointments("status = ?", ("booked",))
    ids = {a.id for a in booked}
    assert {a1.id, a2.id} <= ids

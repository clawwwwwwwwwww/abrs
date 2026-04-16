"""Insert demo appointments at known offsets so we can exercise reminders.tick.

Four rows, each engineered to trigger exactly one job:
  - a1: starts in +24h, status=booked, no flags         → 24h reminder
  - a2: starts in +2h, status=booked, 24h flag set      → 2h reminder
  - a3: 30-min appt starting 90m ago (ended 60m ago),
        status=booked, both reminder flags set          → follow-up + completed
  - a4: 30-min appt starting 2h ago (ended 90m ago,
        outside follow-up window), status=booked,
        both reminder flags set                         → no_show + owner notice
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app import db


async def seed():
    await db.migrate()
    now = datetime.now(timezone.utc)

    a1 = await db.create_appointment(
        name="Ada", phone="+15550000001", service="Consultation",
        when=now + timedelta(hours=24), duration_minutes=30, calendar_event_id="evt-a1",
    )

    a2 = await db.create_appointment(
        name="Babbage", phone="+15550000002", service="Consultation",
        when=now + timedelta(hours=2), duration_minutes=30, calendar_event_id="evt-a2",
    )
    await db.mark_flag(a2.id, "reminder_24h_sent")

    a3 = await db.create_appointment(
        name="Curie", phone="+15550000003", service="Treatment",
        when=now - timedelta(minutes=90), duration_minutes=30, calendar_event_id="evt-a3",
    )
    await db.mark_flag(a3.id, "reminder_24h_sent")
    await db.mark_flag(a3.id, "reminder_2h_sent")

    a4 = await db.create_appointment(
        name="Darwin", phone="+15550000004", service="Consultation",
        when=now - timedelta(hours=2), duration_minutes=30, calendar_event_id="evt-a4",
    )
    await db.mark_flag(a4.id, "reminder_24h_sent")
    await db.mark_flag(a4.id, "reminder_2h_sent")

    return [a1.id, a2.id, a3.id, a4.id]


if __name__ == "__main__":
    asyncio.run(seed())

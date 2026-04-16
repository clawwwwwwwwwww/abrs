"""Top-level dispatch for incoming WhatsApp messages."""
from __future__ import annotations

import os

from app import admin, booking, db
from tools import gcal
from tools.config import BusinessConfig
from tools.whatsapp import Inbound, send_text


async def route(inb: Inbound, cfg: BusinessConfig) -> None:
    # 1) Owner admin commands first (only if from OWNER_WHATSAPP).
    owner = os.environ.get("OWNER_WHATSAPP", "").strip()
    if owner and inb.from_phone == owner and inb.type == "text":
        if await admin.handle(inb):
            return

    # 2) Reminder button replies (Confirm / Reschedule from a 24h reminder).
    if inb.type == "button" and inb.reply_id:
        if inb.reply_id.startswith("confirm:"):
            appt_id = inb.reply_id.split(":", 1)[1]
            await db.update_appointment_status(appt_id, "confirmed")
            await send_text(inb.from_phone, "Confirmed. See you then!")
            return
        if inb.reply_id.startswith("reschedule:"):
            appt_id = inb.reply_id.split(":", 1)[1]
            appt = await db.get_appointment(appt_id)
            if appt and appt.calendar_event_id:
                try:
                    await gcal.cancel_event(appt.calendar_event_id)
                except Exception:
                    pass  # event may already be gone
            if appt:
                await db.update_appointment_status(appt_id, "cancelled")
            await send_text(inb.from_phone, "Cancelled. Let's pick a new time.")
            await booking.handle(
                Inbound(from_phone=inb.from_phone, type="text", text="Book"), cfg
            )
            return

    # 3) Booking conversation.
    if await booking.handle(inb, cfg):
        return

    # 4) Fallthrough: gentle help.
    await send_text(
        inb.from_phone,
        "Send 'Book' to schedule an appointment.",
    )

"""Booking conversation state machine.

States stored in `sessions` table:
  AWAITING_SERVICE  → user picks a service from a list
  AWAITING_SLOT     → user picks a time from a list
  AWAITING_NAME     → new user types their name

Reply ids encoded as:
  service:<service name>
  slot:<ISO8601 UTC>
  more_slots
  cancel:appt:<id>   (used by cancel-from-reminder; handled in router)
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import re

from app import db
from tools import gcal
from tools.config import BusinessConfig
from tools.slots import compute_available_slots, BusyInterval
from tools.whatsapp import Inbound, ListRow, send_list, send_text

BOOKING_KEYWORDS = re.compile(r"^\s*(book|appointment|schedule)\s*$", re.I)
CANCEL_KEYWORDS = re.compile(r"^\s*cancel\s*$", re.I)
SLOTS_PER_PAGE = 9  # leave 1 row for "More"


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "UTC"))


def _fmt_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(_local_tz()).strftime("%a %b %d, %I:%M %p %Z")


# ---------- entry point ----------

async def handle(inb: Inbound, cfg: BusinessConfig) -> bool:
    """Returns True if the message was handled by the booking flow."""
    if inb.type == "text" and CANCEL_KEYWORDS.match(inb.text or ""):
        await db.delete_session(inb.from_phone)
        await send_text(inb.from_phone, "Cancelled. Send 'Book' to start over.")
        return True

    if inb.type == "text" and BOOKING_KEYWORDS.match(inb.text or ""):
        await _start(inb.from_phone, cfg)
        return True

    sess = await db.get_session(inb.from_phone)
    if not sess:
        return False

    if sess.state == "AWAITING_SERVICE" and inb.type == "list" and (inb.reply_id or "").startswith("service:"):
        await _on_service_picked(inb.from_phone, inb.reply_id.split(":", 1)[1], cfg)
        return True

    if sess.state == "AWAITING_SLOT" and inb.type == "list":
        if inb.reply_id == "more_slots":
            await _send_slot_page(inb.from_phone, cfg, page=sess.data.get("slot_page", 0) + 1)
            return True
        if (inb.reply_id or "").startswith("slot:"):
            await _on_slot_picked(inb.from_phone, inb.reply_id.split(":", 1)[1], cfg)
            return True

    if sess.state == "AWAITING_NAME" and inb.type == "text":
        await _on_name_provided(inb.from_phone, (inb.text or "").strip(), cfg)
        return True

    return False


# ---------- steps ----------

async def _start(phone: str, cfg: BusinessConfig) -> None:
    rows = [
        ListRow(id=f"service:{s.name}", title=s.name, description=f"{s.duration_minutes} min")
        for s in cfg.services[:10]
    ]
    await send_list(phone, "What service would you like to book?", "Pick service",
                    rows, section_title="Services")
    await db.set_session(phone, "AWAITING_SERVICE", {})


async def _on_service_picked(phone: str, service_name: str, cfg: BusinessConfig) -> None:
    svc = cfg.service_by_name(service_name)
    if svc is None:
        await send_text(phone, f"Sorry, I don't recognize '{service_name}'. Send 'Book' to try again.")
        await db.delete_session(phone)
        return
    await db.set_session(phone, "AWAITING_SLOT", {
        "service": svc.name,
        "duration_minutes": svc.duration_minutes,
        "slot_page": 0,
    })
    await _send_slot_page(phone, cfg, page=0)


async def _send_slot_page(phone: str, cfg: BusinessConfig, page: int) -> None:
    sess = await db.get_session(phone)
    if not sess:
        return
    from datetime import timedelta
    duration = int(sess.data["duration_minutes"])
    now = datetime.now(timezone.utc)
    busy_intervals = await gcal.list_busy(now, now + timedelta(days=8))

    all_slots = compute_available_slots(
        duration_minutes=duration,
        config=cfg,
        busy=[BusyInterval(b.start, b.end) for b in busy_intervals],
        now=now,
        max_slots=SLOTS_PER_PAGE * (page + 1) + 1,  # +1 to know if there's a next page
    )
    page_slots = all_slots[page * SLOTS_PER_PAGE : (page + 1) * SLOTS_PER_PAGE]
    has_more = len(all_slots) > (page + 1) * SLOTS_PER_PAGE

    if not page_slots:
        await send_text(phone, "No slots available in the next 7 days. Try again later.")
        await db.delete_session(phone)
        return

    rows = [
        ListRow(id=f"slot:{s.astimezone(timezone.utc).isoformat()}",
                title=_fmt_local(s)[:24],
                description=f"{duration} min")
        for s in page_slots
    ]
    if has_more:
        rows.append(ListRow(id="more_slots", title="More times…", description="Show next page"))

    sess.data["slot_page"] = page
    await db.set_session(phone, "AWAITING_SLOT", sess.data)
    await send_list(phone, f"Pick a time for your {sess.data['service']}.", "Pick time",
                    rows, section_title="Available")


async def _on_slot_picked(phone: str, slot_iso: str, cfg: BusinessConfig) -> None:
    sess = await db.get_session(phone)
    if not sess:
        return
    sess.data["slot"] = slot_iso
    await db.set_session(phone, sess.state, sess.data)

    user = await db.get_user(phone)
    if user is None:
        await db.set_session(phone, "AWAITING_NAME", sess.data)
        await send_text(phone, "Got it. What name should I put this under?")
        return
    await _confirm(phone, user["name"], cfg)


async def _on_name_provided(phone: str, name: str, cfg: BusinessConfig) -> None:
    if not name or len(name) > 100:
        await send_text(phone, "Please send your name as a short message.")
        return
    await db.upsert_user(phone, name)
    await _confirm(phone, name, cfg)


async def _confirm(phone: str, name: str, cfg: BusinessConfig) -> None:
    from datetime import timedelta
    sess = await db.get_session(phone)
    if not sess:
        return
    when = db.from_iso(sess.data["slot"])
    duration = int(sess.data["duration_minutes"])
    service = sess.data["service"]

    eid = await gcal.create_event(
        summary=f"{service} — {name}",
        description=f"Booked via WhatsApp.\nPhone: {phone}",
        start=when,
        end=when + timedelta(minutes=duration),
    )
    appt = await db.create_appointment(
        name=name, phone=phone, service=service,
        when=when, duration_minutes=duration, calendar_event_id=eid,
    )
    await db.delete_session(phone)
    await send_text(
        phone,
        (f"You're booked!\n"
         f"Service: {service}\n"
         f"When: {_fmt_local(when)}\n"
         f"Where: {cfg.location.address}\n"
         f"Booking ID: {appt.id[:8]}\n\n"
         f"Reply 'cancel' to cancel."),
    )

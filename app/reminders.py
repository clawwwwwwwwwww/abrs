"""Reminder engine — runs every 15 minutes via APScheduler.

Four jobs, each idempotent (boolean flags + status transitions guard against repeats):
  1. 24h reminder  — Confirm / Reschedule buttons
  2. 2h reminder   — final note with location/parking/access
  3. Follow-up     — "How was your visit?" + review link, marks completed
  4. No-show       — flips status to no_show, notifies owner

Run once for tests / manual ops:
  python -m app.reminders --once
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db
from tools.config import BusinessConfig, load_business_config
from tools.whatsapp import Button, send_buttons, send_text

log = logging.getLogger("abrs.reminders")


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "UTC"))


def _fmt_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(_local_tz()).strftime("%a %b %d, %I:%M %p %Z")


async def tick(cfg: BusinessConfig) -> None:
    now = datetime.now(timezone.utc)
    await _send_24h(now)
    await _send_2h(now, cfg)
    await _send_followups(now, cfg)
    await _flag_no_shows(now)


# ---- 1. 24h reminder ----
async def _send_24h(now: datetime) -> None:
    lo = db.to_iso(now + timedelta(hours=23, minutes=45))
    hi = db.to_iso(now + timedelta(hours=24, minutes=15))
    appts = await db.query_appointments(
        "datetime BETWEEN ? AND ? AND status = 'booked' AND reminder_24h_sent = 0",
        (lo, hi),
    )
    for a in appts:
        await send_buttons(
            a.phone,
            (f"Reminder: your {a.service} is tomorrow at {_fmt_local(db.from_iso(a.datetime))}.\n"
             f"Are we still on?"),
            buttons=[
                Button(id=f"confirm:{a.id}", title="Confirm"),
                Button(id=f"reschedule:{a.id}", title="Reschedule"),
            ],
        )
        await db.mark_flag(a.id, "reminder_24h_sent")
        log.info("24h reminder sent for %s", a.id)


# ---- 2. 2h reminder ----
async def _send_2h(now: datetime, cfg: BusinessConfig) -> None:
    lo = db.to_iso(now + timedelta(hours=1, minutes=45))
    hi = db.to_iso(now + timedelta(hours=2, minutes=15))
    appts = await db.query_appointments(
        "datetime BETWEEN ? AND ? AND status IN ('booked','confirmed') AND reminder_2h_sent = 0",
        (lo, hi),
    )
    for a in appts:
        await send_text(
            a.phone,
            (f"See you in ~2 hours for your {a.service} at {_fmt_local(db.from_iso(a.datetime))}.\n"
             f"Address: {cfg.location.address}\n"
             f"Parking: {cfg.location.parking}\n"
             f"Access: {cfg.location.access_notes}"),
        )
        await db.mark_flag(a.id, "reminder_2h_sent")
        log.info("2h reminder sent for %s", a.id)


# ---- 3. Follow-up ----
async def _send_followups(now: datetime, cfg: BusinessConfig) -> None:
    """Appointment ended ~1h ago. We don't store end-time directly, so query in Python
    after pulling a small candidate set."""
    earliest_start = db.to_iso(now - timedelta(hours=4))
    latest_start = db.to_iso(now - timedelta(minutes=15))
    candidates = await db.query_appointments(
        "datetime BETWEEN ? AND ? AND status IN ('booked','confirmed') AND followup_sent = 0",
        (earliest_start, latest_start),
    )
    window_lo = now - timedelta(hours=1, minutes=15)
    window_hi = now - timedelta(minutes=45)
    for a in candidates:
        end = db.from_iso(a.datetime) + timedelta(minutes=a.duration_minutes)
        if not (window_lo <= end <= window_hi):
            continue
        await send_buttons(
            a.phone,
            f"Hope your {a.service} went well! How was your visit? A 5-star review really helps us out.",
            buttons=[Button(id=f"review:{a.id}", title="Leave a review")],
        )
        # Buttons can carry a URL via reply only; for a clickable link we also send the URL as text.
        if cfg.location.review_url:
            await send_text(a.phone, f"Review link: {cfg.location.review_url}")
        await db.mark_flag(a.id, "followup_sent")
        await db.update_appointment_status(a.id, "completed")
        log.info("follow-up sent + completed for %s", a.id)


# ---- 4. No-show detection ----
async def _flag_no_shows(now: datetime) -> None:
    cutoff = db.to_iso(now - timedelta(hours=1))
    candidates = await db.query_appointments(
        "datetime < ? AND status = 'booked' AND reminder_2h_sent = 1",
        (cutoff,),
    )
    owner = os.environ.get("OWNER_WHATSAPP", "").strip()
    for a in candidates:
        await db.update_appointment_status(a.id, "no_show")
        log.info("flagged no_show for %s", a.id)
        if owner:
            await send_text(
                owner,
                (f"No-show: {a.name} ({a.phone}) — {a.service} at "
                 f"{_fmt_local(db.from_iso(a.datetime))}."),
            )


# ---- CLI for manual / test runs ----
async def _run_once() -> None:
    await db.migrate()
    cfg = load_business_config(os.environ.get("BUSINESS_CONFIG_PATH", "config/business.yaml"))
    await tick(cfg)


def _main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="run a single tick and exit")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        asyncio.run(_run_once())
    else:
        raise SystemExit("use --once or run via the scheduler in the FastAPI app")


if __name__ == "__main__":
    _main()

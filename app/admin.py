"""Owner-only admin commands: Today / Tomorrow / Stats."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import db
from tools.whatsapp import Inbound, send_text

CMDS = {"today", "tomorrow", "stats"}


def _local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "UTC"))


def _local_day_bounds_utc(offset_days: int) -> tuple[datetime, datetime]:
    tz = _local_tz()
    now_local = datetime.now(tz) + timedelta(days=offset_days)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _fmt(dt_iso: str) -> str:
    dt = db.from_iso(dt_iso).astimezone(_local_tz())
    return dt.strftime("%I:%M %p")


async def handle(inb: Inbound) -> bool:
    cmd = (inb.text or "").strip().lower()
    if cmd not in CMDS:
        return False

    if cmd in {"today", "tomorrow"}:
        start, end = _local_day_bounds_utc(0 if cmd == "today" else 1)
        appts = await db.query_appointments(
            "datetime >= ? AND datetime < ? ORDER BY datetime",
            (db.to_iso(start), db.to_iso(end)),
        )
        if not appts:
            await send_text(inb.from_phone, f"No appointments {cmd}.")
            return True
        lines = [f"{cmd.capitalize()}'s appointments:"]
        for a in appts:
            lines.append(f"• {_fmt(a.datetime)} — {a.name} ({a.service}) [{a.status}]")
        await send_text(inb.from_phone, "\n".join(lines))
        return True

    # stats: this ISO week (Mon..Sun) in local tz
    tz = _local_tz()
    today_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start_local = today_local - timedelta(days=today_local.weekday())
    week_end_local = week_start_local + timedelta(days=7)
    week_start = week_start_local.astimezone(timezone.utc)
    week_end = week_end_local.astimezone(timezone.utc)

    in_week = await db.query_appointments(
        "datetime >= ? AND datetime < ?", (db.to_iso(week_start), db.to_iso(week_end))
    )
    total = len(in_week)
    completed = sum(1 for a in in_week if a.status == "completed")
    no_show = sum(1 for a in in_week if a.status == "no_show")
    cancelled = sum(1 for a in in_week if a.status == "cancelled")
    denom = completed + no_show + cancelled
    rate = (completed / denom * 100.0) if denom else 0.0

    await send_text(
        inb.from_phone,
        (f"This week ({week_start_local.date()} – {(week_end_local - timedelta(days=1)).date()}):\n"
         f"• Bookings: {total}\n"
         f"• Completed: {completed}\n"
         f"• No-shows: {no_show}\n"
         f"• Cancelled: {cancelled}\n"
         f"• Completion rate: {rate:.0f}%"),
    )
    return True

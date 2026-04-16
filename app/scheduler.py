"""APScheduler boot. Hooked to the reminder engine (stage 6)."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tools.config import BusinessConfig

log = logging.getLogger("abrs.scheduler")


def start_scheduler(cfg: BusinessConfig) -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(
        _tick_wrapper,
        trigger="interval",
        minutes=15,
        kwargs={"cfg": cfg},
        id="reminders.tick",
        replace_existing=True,
        next_run_time=None,  # don't fire immediately on boot
    )
    sched.start()
    log.info("scheduler started: reminders.tick every 15 minutes")
    return sched


async def _tick_wrapper(cfg: BusinessConfig) -> None:
    from app.reminders import tick
    try:
        await tick(cfg)
    except Exception:
        log.exception("reminders.tick failed")

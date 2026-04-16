"""FastAPI entrypoint: WhatsApp webhook + scheduler boot."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response

from app import db, router
from app.reminders import tick as reminder_tick
from tools.config import load_business_config
from tools.whatsapp import parse_incoming, verify_signature

log = logging.getLogger("abrs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.migrate()
    app.state.cfg = load_business_config(os.environ.get("BUSINESS_CONFIG_PATH", "config/business.yaml"))
    # Start scheduler unless explicitly disabled (tests + production-with-external-cron disable it).
    if os.environ.get("DISABLE_SCHEDULER", "0") != "1":
        from app.scheduler import start_scheduler
        app.state.scheduler = start_scheduler(app.state.cfg)
    yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)
    await db.close_pool()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "stub": os.environ.get("STUB_MODE", "1") == "1"}


@app.post("/cron/tick")
async def cron_tick(request: Request):
    """Trigger a single reminder tick. Auth via X-Cron-Secret header.

    Used in production where APScheduler is disabled and cron-job.org (or any
    external scheduler) hits this endpoint every 15 minutes.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret") != expected:
        raise HTTPException(status_code=403, detail="forbidden")
    await reminder_tick(request.app.state.cfg)
    return {"ok": True}


@app.get("/webhook")
async def webhook_verify(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
):
    """Meta webhook verification handshake."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if hub_mode == "subscribe" and expected and hub_verify_token == expected:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="verification failed")


@app.post("/webhook")
async def webhook_event(request: Request):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="bad signature")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    inb = parse_incoming(payload)
    if inb is None:
        return {"ok": True}  # status callbacks etc. — ack and ignore

    try:
        await router.route(inb, request.app.state.cfg)
    except Exception as exc:
        log.exception("router error: %s", exc)
        # Always 200 so Meta doesn't redeliver indefinitely.
    return {"ok": True}

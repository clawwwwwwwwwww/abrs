"""Google Calendar adapter with a stub backend for free local testing.

Stub mode (STUB_MODE=1):
  - State lives in .tmp/calendar.json: {"events": [{"id","summary","start","end","description"}]}
  - list_busy returns intervals from this file.
  - create_event / cancel_event mutate this file.

Real mode:
  - Uses google-api-python-client with a service account from GOOGLE_CREDS (JSON in env).
  - GOOGLE_CALENDAR_ID identifies the target calendar; service account must have access.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.db import to_iso, from_iso  # reuse ISO8601 helpers

ROOT = Path(__file__).resolve().parent.parent
STUB_FILE = ROOT / ".tmp" / "calendar.json"


def is_stub() -> bool:
    return os.environ.get("STUB_MODE", "1") == "1"


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime


# ---------- stub backend ----------

def _stub_load() -> dict:
    if not STUB_FILE.exists():
        return {"events": []}
    return json.loads(STUB_FILE.read_text())


def _stub_save(state: dict) -> None:
    STUB_FILE.parent.mkdir(parents=True, exist_ok=True)
    STUB_FILE.write_text(json.dumps(state, indent=2))


# ---------- public API ----------

async def list_busy(start: datetime, end: datetime) -> List[Busy]:
    if is_stub():
        out: List[Busy] = []
        for e in _stub_load()["events"]:
            es, ee = from_iso(e["start"]), from_iso(e["end"])
            if es < end and start < ee:
                out.append(Busy(es, ee))
        return out

    service = _gcal_service()
    cal_id = os.environ["GOOGLE_CALENDAR_ID"]
    body = {
        "timeMin": to_iso(start),
        "timeMax": to_iso(end),
        "items": [{"id": cal_id}],
    }
    fb = service.freebusy().query(body=body).execute()
    intervals = fb["calendars"][cal_id].get("busy", [])
    return [Busy(from_iso(b["start"]), from_iso(b["end"])) for b in intervals]


async def create_event(
    *,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
) -> str:
    """Returns the event id."""
    if is_stub():
        state = _stub_load()
        eid = "stub.evt." + uuid.uuid4().hex[:12]
        state["events"].append({
            "id": eid,
            "summary": summary,
            "description": description,
            "start": to_iso(start),
            "end": to_iso(end),
        })
        _stub_save(state)
        return eid

    service = _gcal_service()
    cal_id = os.environ["GOOGLE_CALENDAR_ID"]
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": to_iso(start)},
        "end": {"dateTime": to_iso(end)},
    }
    evt = service.events().insert(calendarId=cal_id, body=body).execute()
    return evt["id"]


async def cancel_event(event_id: str) -> None:
    if is_stub():
        state = _stub_load()
        state["events"] = [e for e in state["events"] if e["id"] != event_id]
        _stub_save(state)
        return

    service = _gcal_service()
    cal_id = os.environ["GOOGLE_CALENDAR_ID"]
    service.events().delete(calendarId=cal_id, eventId=event_id).execute()


# ---------- real backend wiring (lazy import so stub mode needs no google libs) ----------

def _gcal_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_json = os.environ["GOOGLE_CREDS"]
    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

"""WhatsApp Cloud API client + inbound parser.

Stub mode (STUB_MODE=1, default in dev):
  - Outbound sends append a JSON line to .tmp/outbox.jsonl and return a fake wamid.
  - HMAC verification is skipped (so fake_inbound.py works without app-secret signing).

Real mode (STUB_MODE=0):
  - Outbound sends POST to https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages.
  - Inbound HMAC verified via X-Hub-Signature-256 against WHATSAPP_APP_SECRET.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / ".tmp" / "outbox.jsonl"
GRAPH_BASE = "https://graph.facebook.com/v20.0"


def is_stub() -> bool:
    return os.environ.get("STUB_MODE", "1") == "1"


# ---------- outbound ----------

def _record_stub(payload: Dict[str, Any]) -> Dict[str, Any]:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    wamid = "stub.wamid." + uuid.uuid4().hex[:12]
    record = {"ts": time.time(), "wamid": wamid, "payload": payload}
    with OUTBOX.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return {"messaging_product": "whatsapp", "messages": [{"id": wamid}]}


async def _post(payload: Dict[str, Any]) -> Dict[str, Any]:
    if is_stub():
        return _record_stub(payload)
    token = os.environ["WHATSAPP_TOKEN"]
    phone_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    url = f"{GRAPH_BASE}/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


async def send_text(to: str, body: str) -> Dict[str, Any]:
    return await _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": False},
    })


@dataclass
class Button:
    id: str   # callback id we'll receive back
    title: str  # max 20 chars per Cloud API


async def send_buttons(to: str, body: str, buttons: List[Button]) -> Dict[str, Any]:
    if not 1 <= len(buttons) <= 3:
        raise ValueError("WhatsApp interactive buttons must be 1..3")
    return await _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title[:20]}}
                    for b in buttons
                ]
            },
        },
    })


@dataclass
class ListRow:
    id: str
    title: str  # max 24 chars
    description: str = ""  # max 72 chars


async def send_list(
    to: str,
    body: str,
    button_label: str,
    rows: List[ListRow],
    section_title: str = "Options",
    header: Optional[str] = None,
) -> Dict[str, Any]:
    if not 1 <= len(rows) <= 10:
        raise ValueError("WhatsApp list message supports 1..10 rows per section")
    interactive: Dict[str, Any] = {
        "type": "list",
        "body": {"text": body},
        "action": {
            "button": button_label[:20],
            "sections": [{
                "title": section_title[:24],
                "rows": [
                    {"id": r.id, "title": r.title[:24], "description": r.description[:72]}
                    for r in rows
                ],
            }],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    return await _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    })


# ---------- inbound ----------

@dataclass
class Inbound:
    from_phone: str
    type: str          # "text" | "button" | "list" | "unknown"
    text: Optional[str] = None
    reply_id: Optional[str] = None  # button id or list-row id
    raw: Optional[Dict[str, Any]] = None


def parse_incoming(payload: Dict[str, Any]) -> Optional[Inbound]:
    """Turn Meta's webhook envelope into a normalized Inbound (or None if irrelevant)."""
    try:
        change = payload["entry"][0]["changes"][0]["value"]
        msgs = change.get("messages")
        if not msgs:
            return None  # status callbacks etc.
        msg = msgs[0]
        from_phone = "+" + msg["from"] if not msg["from"].startswith("+") else msg["from"]
    except (KeyError, IndexError, TypeError):
        return None

    mtype = msg.get("type")
    if mtype == "text":
        return Inbound(from_phone=from_phone, type="text", text=msg["text"]["body"], raw=msg)
    if mtype == "interactive":
        inter = msg["interactive"]
        if inter.get("type") == "button_reply":
            br = inter["button_reply"]
            return Inbound(from_phone=from_phone, type="button",
                           reply_id=br["id"], text=br.get("title"), raw=msg)
        if inter.get("type") == "list_reply":
            lr = inter["list_reply"]
            return Inbound(from_phone=from_phone, type="list",
                           reply_id=lr["id"], text=lr.get("title"), raw=msg)
    return Inbound(from_phone=from_phone, type="unknown", raw=msg)


def verify_signature(raw_body: bytes, header: Optional[str]) -> bool:
    """Verify Meta's X-Hub-Signature-256 header. In stub mode, accept anything."""
    if is_stub():
        return True
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)

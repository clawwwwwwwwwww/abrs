"""POST a synthetic webhook payload to a running ABRS instance.

Usage:
  python scripts/fake_inbound.py book                         # text "Book"
  python scripts/fake_inbound.py text "any text" --from +15551234567
  python scripts/fake_inbound.py list service:Consultation
  python scripts/fake_inbound.py button confirm:appt-abc123

Requires the server to be running (uvicorn app.main:app) on localhost:8000 by default.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict

import httpx

DEFAULT_URL = "http://localhost:8000/webhook"
DEFAULT_FROM = "15550000001"  # no leading + here; Meta's payload format omits it


def _envelope(message: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "FAKE_WABA",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "15550009999", "phone_number_id": "PHONE_ID"},
                    "messages": [message],
                },
                "field": "messages",
            }],
        }],
    }


def build(kind: str, body: str, from_phone: str) -> Dict[str, Any]:
    base = {"from": from_phone.lstrip("+"), "id": f"wamid.fake.{int(time.time()*1000)}",
            "timestamp": str(int(time.time()))}
    if kind == "text" or kind == "book":
        base.update({"type": "text", "text": {"body": "Book" if kind == "book" else body}})
    elif kind == "list":
        base.update({"type": "interactive",
                     "interactive": {"type": "list_reply",
                                     "list_reply": {"id": body, "title": body, "description": ""}}})
    elif kind == "button":
        base.update({"type": "interactive",
                     "interactive": {"type": "button_reply",
                                     "button_reply": {"id": body, "title": body[:20]}}})
    else:
        raise SystemExit(f"unknown kind {kind!r}")
    return _envelope(base)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["book", "text", "list", "button"])
    p.add_argument("body", nargs="?", default="")
    p.add_argument("--from", dest="from_phone", default=DEFAULT_FROM)
    p.add_argument("--url", default=DEFAULT_URL)
    args = p.parse_args()

    payload = build(args.kind, args.body, args.from_phone)
    r = httpx.post(args.url, json=payload, timeout=15.0)
    print(f"{r.status_code} {r.text}")
    print(json.dumps(payload, indent=2))
    return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())

"""End-to-end stub demo. Runs the booking flow + reminder tick + admin commands
back-to-back against a temporary SQLite DB and prints a pass/fail summary.

Run:  python3 scripts/demo_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_env(tmp: Path):
    os.environ["STUB_MODE"] = "1"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp / 'app.db'}"
    os.environ["DISABLE_SCHEDULER"] = "1"
    os.environ["TZ"] = "UTC"
    os.environ["BUSINESS_CONFIG_PATH"] = str(ROOT / "config" / "business.yaml")
    os.environ["OWNER_WHATSAPP"] = "+15559999999"


def _redirect(tmp: Path):
    from tools import whatsapp, gcal
    whatsapp.OUTBOX = tmp / "outbox.jsonl"
    gcal.STUB_FILE = tmp / "calendar.json"


def _outbox(tmp: Path):
    f = tmp / "outbox.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def _envelope(msg):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "x", "phone_number_id": "y"},
                "messages": [msg]}, "field": "messages"}]}]}


async def _walk_booking(client):
    # Step 1
    r = client.post("/webhook", json=_envelope({
        "from": "15550000001", "id": "1", "timestamp": "1",
        "type": "text", "text": {"body": "Book"}}))
    assert r.status_code == 200
    # Step 2
    r = client.post("/webhook", json=_envelope({
        "from": "15550000001", "id": "2", "timestamp": "2", "type": "interactive",
        "interactive": {"type": "list_reply",
                        "list_reply": {"id": "service:Consultation", "title": "Consultation"}}}))
    # Pull the slot list from the outbox to grab a real slot id.
    from pathlib import Path as _P
    out = json.loads(_P(os.environ["__DEMO_TMP"]) .joinpath("outbox.jsonl").read_text().splitlines()[-1])
    slot_id = next(r["id"] for r in out["payload"]["interactive"]["action"]["sections"][0]["rows"]
                   if r["id"].startswith("slot:"))
    # Step 3
    client.post("/webhook", json=_envelope({
        "from": "15550000001", "id": "3", "timestamp": "3", "type": "interactive",
        "interactive": {"type": "list_reply",
                        "list_reply": {"id": slot_id, "title": "slot"}}}))
    # Step 4
    client.post("/webhook", json=_envelope({
        "from": "15550000001", "id": "4", "timestamp": "4",
        "type": "text", "text": {"body": "Ada Lovelace"}}))


async def main():
    failures = []
    with tempfile.TemporaryDirectory() as tdir:
        tmp = Path(tdir)
        _setup_env(tmp)
        os.environ["__DEMO_TMP"] = str(tmp)
        _redirect(tmp)

        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        from app.reminders import tick
        from tools.config import load_business_config
        from scripts.seed_demo import seed

        with TestClient(app) as client:
            print("\n=== Stage A: Booking flow ===")
            await _walk_booking(client)
            appts = await db.query_appointments("phone = ?", ("+15550000001",))
            if len(appts) == 1 and appts[0].status == "booked":
                print(f"  ✓ booking created: id={appts[0].id[:8]} service={appts[0].service}")
            else:
                failures.append("booking did not create exactly 1 appointment")

            print("\n=== Stage B: Reminder engine ===")
            await seed()
            cfg = load_business_config(ROOT / "config" / "business.yaml")
            await tick(cfg)
            payloads = [p["payload"] for p in _outbox(tmp)]
            seen_24h = any(p["to"] == "+15550000001" and p.get("interactive", {}).get("type") == "button"
                           for p in payloads)
            seen_2h = any(p["to"] == "+15550000002" and p["type"] == "text" for p in payloads)
            seen_followup = any(p["to"] == "+15550000003" for p in payloads)
            seen_noshow = any(p["to"] == "+15559999999" and "No-show" in p.get("text", {}).get("body", "")
                              for p in payloads)
            for label, ok in [("24h reminder", seen_24h), ("2h reminder", seen_2h),
                               ("follow-up", seen_followup), ("no-show owner notice", seen_noshow)]:
                print(f"  {'✓' if ok else '✗'} {label}")
                if not ok:
                    failures.append(f"missing: {label}")

            print("\n=== Stage C: Admin ===")
            for cmd in ("Today", "Tomorrow", "Stats"):
                client.post("/webhook", json=_envelope({
                    "from": "15559999999", "id": cmd, "timestamp": "9",
                    "type": "text", "text": {"body": cmd}}))
            last_three = [p["payload"]["text"]["body"] for p in _outbox(tmp)[-3:]]
            for body, label in zip(last_three, ("Today", "Tomorrow", "Stats")):
                head = body.splitlines()[0][:60]
                print(f"  ✓ {label}: {head}")

    print("\n=== Summary ===")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All stages passed in stub mode. Zero external API calls.")


if __name__ == "__main__":
    asyncio.run(main())

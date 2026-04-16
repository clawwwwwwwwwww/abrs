"""End-to-end booking flow against the FastAPI app, all in stub mode.

Walks: 'Book' → service list reply → slot list reply → name → confirmation.
Asserts: appointment row, calendar event, outbox messages.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BUSINESS_CONFIG_PATH", str(ROOT / "config" / "business.yaml"))

    # Redirect stub side-effects into tmp.
    from tools import whatsapp, gcal
    monkeypatch.setattr(whatsapp, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(gcal, "STUB_FILE", tmp_path / "calendar.json")

    from app.main import app
    with TestClient(app) as c:
        c.outbox = tmp_path / "outbox.jsonl"
        c.calendar = tmp_path / "calendar.json"
        yield c


def _envelope(msg):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "x", "phone_number_id": "y"},
                    "messages": [msg],
                },
                "field": "messages",
            }],
        }],
    }


def _text(body, from_="15550000001"):
    return _envelope({"from": from_, "id": "wamid.t", "timestamp": "1",
                      "type": "text", "text": {"body": body}})


def _list_reply(reply_id, from_="15550000001"):
    return _envelope({"from": from_, "id": "wamid.l", "timestamp": "1", "type": "interactive",
                      "interactive": {"type": "list_reply",
                                      "list_reply": {"id": reply_id, "title": reply_id, "description": ""}}})


def _outbox(client):
    return [json.loads(l) for l in client.outbox.read_text().splitlines() if l.strip()]


def _last_payload(client):
    return _outbox(client)[-1]["payload"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_full_booking_flow(client):
    # 1) "Book" → service list
    r = client.post("/webhook", json=_text("Book"))
    assert r.status_code == 200
    payload = _last_payload(client)
    assert payload["type"] == "interactive"
    assert payload["interactive"]["type"] == "list"
    rows = payload["interactive"]["action"]["sections"][0]["rows"]
    service_ids = [row["id"] for row in rows]
    assert "service:Consultation" in service_ids

    # 2) Pick service → slot list
    r = client.post("/webhook", json=_list_reply("service:Consultation"))
    assert r.status_code == 200
    slot_payload = _last_payload(client)
    assert slot_payload["interactive"]["type"] == "list"
    slot_rows = slot_payload["interactive"]["action"]["sections"][0]["rows"]
    slot_ids = [row["id"] for row in slot_rows if row["id"].startswith("slot:")]
    assert slot_ids, "should have at least one slot"
    chosen = slot_ids[0]

    # 3) Pick slot → name prompt (new user)
    r = client.post("/webhook", json=_list_reply(chosen))
    assert r.status_code == 200
    name_prompt = _last_payload(client)
    assert name_prompt["type"] == "text"
    assert "name" in name_prompt["text"]["body"].lower()

    # 4) Provide name → confirmation + calendar event + DB row
    r = client.post("/webhook", json=_text("Ada Lovelace"))
    assert r.status_code == 200
    confirmation = _last_payload(client)
    assert confirmation["type"] == "text"
    body = confirmation["text"]["body"]
    assert "booked" in body.lower()
    assert "Consultation" in body
    assert "Booking ID" in body

    # Calendar event was created
    cal = json.loads(client.calendar.read_text())
    assert len(cal["events"]) == 1
    assert cal["events"][0]["summary"].startswith("Consultation")

    # User stored, session deleted, appointment exists
    import asyncio
    from app import db
    user = asyncio.get_event_loop().run_until_complete(db.get_user("+15550000001"))
    assert user and user["name"] == "Ada Lovelace"
    appts = asyncio.get_event_loop().run_until_complete(db.query_appointments("phone = ?", ("+15550000001",)))
    assert len(appts) == 1
    assert appts[0].service == "Consultation"
    assert appts[0].status == "booked"
    assert appts[0].calendar_event_id == cal["events"][0]["id"]
    sess = asyncio.get_event_loop().run_until_complete(db.get_session("+15550000001"))
    assert sess is None


def test_returning_user_skips_name_prompt(client):
    # Pre-seed user
    import asyncio
    from app import db
    asyncio.get_event_loop().run_until_complete(db.migrate())
    asyncio.get_event_loop().run_until_complete(db.upsert_user("+15550000002", "Returning Customer"))

    client.post("/webhook", json=_text("Book", from_="15550000002"))
    r = client.post("/webhook", json=_list_reply("service:Consultation", from_="15550000002"))
    assert r.status_code == 200
    slot_payload = _last_payload(client)
    slot_rows = slot_payload["interactive"]["action"]["sections"][0]["rows"]
    chosen = next(row["id"] for row in slot_rows if row["id"].startswith("slot:"))

    # Pick slot — returning user should jump straight to confirmation, no name prompt.
    r = client.post("/webhook", json=_list_reply(chosen, from_="15550000002"))
    assert r.status_code == 200
    confirmation = _last_payload(client)
    assert confirmation["type"] == "text"
    assert "booked" in confirmation["text"]["body"].lower()


def test_cancel_clears_session(client):
    client.post("/webhook", json=_text("Book"))
    r = client.post("/webhook", json=_text("cancel"))
    assert r.status_code == 200
    msg = _last_payload(client)
    assert "cancelled" in msg["text"]["body"].lower()

    import asyncio
    from app import db
    sess = asyncio.get_event_loop().run_until_complete(db.get_session("+15550000001"))
    assert sess is None


def test_unknown_message_falls_through_to_help(client):
    r = client.post("/webhook", json=_text("hello"))
    assert r.status_code == 200
    msg = _last_payload(client)
    assert "Book" in msg["text"]["body"]


def test_webhook_verification(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "letmein")
    r = client.get("/webhook", params={"hub.mode": "subscribe",
                                        "hub.challenge": "123456",
                                        "hub.verify_token": "letmein"})
    assert r.status_code == 200
    assert r.text == "123456"

    r = client.get("/webhook", params={"hub.mode": "subscribe",
                                        "hub.challenge": "123456",
                                        "hub.verify_token": "wrong"})
    assert r.status_code == 403

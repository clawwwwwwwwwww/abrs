import hashlib
import hmac
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def stub_outbox(monkeypatch, tmp_path):
    """Redirect the outbox to a per-test temp file and force STUB_MODE=1."""
    monkeypatch.setenv("STUB_MODE", "1")
    from tools import whatsapp
    monkeypatch.setattr(whatsapp, "OUTBOX", tmp_path / "outbox.jsonl")
    yield tmp_path / "outbox.jsonl"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_text_message():
    from tools.whatsapp import parse_incoming
    inb = parse_incoming(_load("inbound_text_book.json"))
    assert inb is not None
    assert inb.from_phone == "+15550000001"
    assert inb.type == "text"
    assert inb.text == "Book"


def test_parse_list_reply():
    from tools.whatsapp import parse_incoming
    inb = parse_incoming(_load("inbound_list_reply.json"))
    assert inb.type == "list"
    assert inb.reply_id == "service:Consultation"
    assert inb.text == "Consultation"


def test_parse_button_reply():
    from tools.whatsapp import parse_incoming
    inb = parse_incoming(_load("inbound_button_reply.json"))
    assert inb.type == "button"
    assert inb.reply_id == "confirm:appt-abc123"


def test_parse_status_callback_returns_none():
    from tools.whatsapp import parse_incoming
    assert parse_incoming(_load("inbound_status_callback.json")) is None


@pytest.mark.asyncio
async def test_send_text_writes_to_outbox(stub_outbox):
    from tools.whatsapp import send_text
    res = await send_text("+15550000001", "Hello")
    assert res["messages"][0]["id"].startswith("stub.wamid.")
    lines = stub_outbox.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["payload"]["type"] == "text"
    assert rec["payload"]["text"]["body"] == "Hello"


@pytest.mark.asyncio
async def test_send_buttons_validates_count(stub_outbox):
    from tools.whatsapp import send_buttons, Button
    with pytest.raises(ValueError):
        await send_buttons("+1", "body", [])
    with pytest.raises(ValueError):
        await send_buttons("+1", "body", [Button("a", "A")] * 4)


@pytest.mark.asyncio
async def test_send_list_writes_payload(stub_outbox):
    from tools.whatsapp import send_list, ListRow
    rows = [ListRow(id=f"slot:{i}", title=f"Slot {i}", description="desc") for i in range(3)]
    await send_list("+15550000001", "Pick a slot", "Choose", rows, section_title="Available")
    rec = json.loads(stub_outbox.read_text().splitlines()[0])
    inter = rec["payload"]["interactive"]
    assert inter["type"] == "list"
    assert inter["action"]["button"] == "Choose"
    assert len(inter["action"]["sections"][0]["rows"]) == 3


def test_signature_verifier_real_mode(monkeypatch):
    from tools import whatsapp
    monkeypatch.setenv("STUB_MODE", "0")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "supersecret")
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(b"supersecret", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(body, sig) is True
    assert whatsapp.verify_signature(body, "sha256=deadbeef") is False
    assert whatsapp.verify_signature(body, None) is False


def test_signature_verifier_stub_mode_accepts_anything(monkeypatch):
    from tools import whatsapp
    monkeypatch.setenv("STUB_MODE", "1")
    assert whatsapp.verify_signature(b"x", None) is True

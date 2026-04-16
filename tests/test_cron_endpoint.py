"""Cron tick endpoint: secret-guarded, fires reminders.tick exactly once."""
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
    monkeypatch.setenv("OWNER_WHATSAPP", "+15559999999")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BUSINESS_CONFIG_PATH", str(ROOT / "config" / "business.yaml"))
    monkeypatch.setenv("CRON_SECRET", "topsecret")
    from tools import whatsapp, gcal
    monkeypatch.setattr(whatsapp, "OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(gcal, "STUB_FILE", tmp_path / "calendar.json")
    from app.main import app
    with TestClient(app) as c:
        c.outbox = tmp_path / "outbox.jsonl"
        yield c


def test_cron_endpoint_requires_secret(client):
    r = client.post("/cron/tick")
    assert r.status_code == 403
    r = client.post("/cron/tick", headers={"X-Cron-Secret": "wrong"})
    assert r.status_code == 403


def test_cron_endpoint_with_correct_secret_fires_tick(client):
    import asyncio
    from scripts.seed_demo import seed
    asyncio.get_event_loop().run_until_complete(seed())

    r = client.post("/cron/tick", headers={"X-Cron-Secret": "topsecret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    payloads = [json.loads(l)["payload"] for l in client.outbox.read_text().splitlines() if l.strip()]
    by_to = {}
    for p in payloads:
        by_to.setdefault(p["to"], []).append(p)
    assert "+15550000001" in by_to  # 24h reminder went out
    assert "+15559999999" in by_to  # owner got no-show notice


def test_cron_endpoint_blocks_when_secret_unset(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    r = client.post("/cron/tick", headers={"X-Cron-Secret": "anything"})
    assert r.status_code == 403

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def stub_calendar(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "1")
    from tools import gcal
    monkeypatch.setattr(gcal, "STUB_FILE", tmp_path / "calendar.json")
    yield


@pytest.mark.asyncio
async def test_create_then_list_busy_includes_event():
    from tools import gcal
    start = datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    eid = await gcal.create_event(
        summary="Consultation - Ada", description="phone +15550000001",
        start=start, end=end,
    )
    assert eid.startswith("stub.evt.")

    busy = await gcal.list_busy(start - timedelta(hours=1), end + timedelta(hours=1))
    assert len(busy) == 1
    assert busy[0].start == start and busy[0].end == end


@pytest.mark.asyncio
async def test_list_busy_filters_window():
    from tools import gcal
    start = datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    await gcal.create_event(summary="A", description="", start=start, end=end)

    far_window_start = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    far_window_end = datetime(2026, 4, 21, 23, 59, tzinfo=timezone.utc)
    busy = await gcal.list_busy(far_window_start, far_window_end)
    assert busy == []


@pytest.mark.asyncio
async def test_cancel_event_removes_it():
    from tools import gcal
    start = datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    eid = await gcal.create_event(summary="A", description="", start=start, end=end)

    await gcal.cancel_event(eid)
    busy = await gcal.list_busy(start - timedelta(hours=1), end + timedelta(hours=1))
    assert busy == []

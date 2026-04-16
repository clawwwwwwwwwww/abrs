"""Async DB layer that speaks both SQLite (dev) and Postgres (prod).

Picks the backend from `DATABASE_URL`:
  sqlite:///.tmp/app.db        → aiosqlite
  postgres://...  /  postgresql://...   → asyncpg

The schema (migrations/001_init.sql) is portable: TEXT for ISO8601 datetimes,
INTEGER for booleans, no DB-specific default expressions. `created_at` and
`updated_at` are always set in Python.

All public functions take/return the same Python types regardless of backend;
internal SQL uses `?` placeholders and the adapter rewrites them to `$1, $2, ...`
when running against Postgres.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations" / "001_init.sql"


# ---------- backend detection ----------

def _backend() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///.tmp/app.db")
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return "pg"
    if url.startswith("sqlite://"):
        return "sqlite"
    raise RuntimeError(f"Unsupported DATABASE_URL scheme: {url!r}")


def _sqlite_path() -> str:
    url = os.environ["DATABASE_URL"] if "DATABASE_URL" in os.environ else "sqlite:///.tmp/app.db"
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///") :]
    else:  # sqlite://
        path = url[len("sqlite://") :]
    if not os.path.isabs(path):
        path = str(ROOT / path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def _pg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    # asyncpg expects "postgres://" or "postgresql://" — both work.
    return url


# ---------- timestamp helpers ----------

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be tz-aware")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


# ---------- placeholder translator ----------

_PLACEHOLDER_RE = re.compile(r"\?")


def _to_pg_placeholders(sql: str) -> str:
    counter = {"i": 0}

    def repl(_):
        counter["i"] += 1
        return f"${counter['i']}"

    return _PLACEHOLDER_RE.sub(repl, sql)


# ---------- connection adapters ----------

class _SqliteConn:
    def __init__(self, raw):
        self._c = raw

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self._c.execute(sql, tuple(params))

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        cur = await self._c.execute(sql, tuple(params))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[dict]:
        cur = await self._c.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def executescript(self, sql: str) -> None:
        await self._c.executescript(sql)


class _PgConn:
    def __init__(self, raw):
        self._c = raw

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self._c.execute(_to_pg_placeholders(sql), *params)

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        row = await self._c.fetchrow(_to_pg_placeholders(sql), *params)
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> List[dict]:
        rows = await self._c.fetch(_to_pg_placeholders(sql), *params)
        return [dict(r) for r in rows]

    async def executescript(self, sql: str) -> None:
        # Run the whole migration in a single multi-statement command.
        # asyncpg.execute() accepts multiple statements separated by ";".
        await self._c.execute(sql)


# ---------- pool / context manager ----------

_pg_pool = None
_pool_lock = asyncio.Lock()


async def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        async with _pool_lock:
            if _pg_pool is None:
                import asyncpg
                _pg_pool = await asyncpg.create_pool(_pg_dsn(), min_size=1, max_size=5)
    return _pg_pool


@asynccontextmanager
async def connect() -> AsyncIterator[Any]:
    if _backend() == "sqlite":
        import aiosqlite
        db = await aiosqlite.connect(_sqlite_path())
        db.row_factory = aiosqlite.Row
        try:
            await db.execute("PRAGMA foreign_keys = ON")
            yield _SqliteConn(db)
            await db.commit()
        finally:
            await db.close()
    else:
        pool = await _get_pg_pool()
        async with pool.acquire() as raw:
            async with raw.transaction():
                yield _PgConn(raw)


async def close_pool() -> None:
    """Call on shutdown to release Postgres connections cleanly."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None


async def migrate() -> None:
    sql = MIGRATIONS.read_text()
    async with connect() as db:
        await db.executescript(sql)


# ---------- appointments ----------

@dataclass
class Appointment:
    id: str
    name: str
    phone: str
    service: str
    datetime: str            # ISO8601 UTC
    duration_minutes: int
    status: str
    calendar_event_id: Optional[str]
    reminder_24h_sent: int
    reminder_2h_sent: int
    followup_sent: int
    created_at: str


def _appt_from_row(row: dict) -> Appointment:
    # Postgres may give us datetime objects or ints/booleans depending on column types,
    # but our portable schema uses TEXT/INTEGER, so values come back as str/int already.
    return Appointment(
        id=row["id"],
        name=row["name"],
        phone=row["phone"],
        service=row["service"],
        datetime=row["datetime"],
        duration_minutes=int(row["duration_minutes"]),
        status=row["status"],
        calendar_event_id=row["calendar_event_id"],
        reminder_24h_sent=int(row["reminder_24h_sent"]),
        reminder_2h_sent=int(row["reminder_2h_sent"]),
        followup_sent=int(row["followup_sent"]),
        created_at=row["created_at"],
    )


async def create_appointment(
    *,
    name: str,
    phone: str,
    service: str,
    when: datetime,
    duration_minutes: int,
    calendar_event_id: Optional[str],
) -> Appointment:
    appt = Appointment(
        id=uuid.uuid4().hex,
        name=name,
        phone=phone,
        service=service,
        datetime=to_iso(when),
        duration_minutes=duration_minutes,
        status="booked",
        calendar_event_id=calendar_event_id,
        reminder_24h_sent=0,
        reminder_2h_sent=0,
        followup_sent=0,
        created_at=utcnow_iso(),
    )
    async with connect() as db:
        await db.execute(
            """INSERT INTO appointments (id,name,phone,service,datetime,duration_minutes,
                                          status,calendar_event_id,reminder_24h_sent,
                                          reminder_2h_sent,followup_sent,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (appt.id, appt.name, appt.phone, appt.service, appt.datetime,
             appt.duration_minutes, appt.status, appt.calendar_event_id,
             appt.reminder_24h_sent, appt.reminder_2h_sent, appt.followup_sent,
             appt.created_at),
        )
    return appt


async def get_appointment(appt_id: str) -> Optional[Appointment]:
    async with connect() as db:
        row = await db.fetchone("SELECT * FROM appointments WHERE id = ?", (appt_id,))
    return _appt_from_row(row) if row else None


async def update_appointment_status(appt_id: str, status: str) -> None:
    async with connect() as db:
        await db.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appt_id))


async def mark_flag(appt_id: str, flag: str) -> None:
    """flag in {reminder_24h_sent, reminder_2h_sent, followup_sent}."""
    if flag not in {"reminder_24h_sent", "reminder_2h_sent", "followup_sent"}:
        raise ValueError(f"bad flag {flag!r}")
    async with connect() as db:
        await db.execute(f"UPDATE appointments SET {flag} = 1 WHERE id = ?", (appt_id,))


async def query_appointments(sql_where: str, params: tuple = ()) -> List[Appointment]:
    async with connect() as db:
        rows = await db.fetchall(f"SELECT * FROM appointments WHERE {sql_where}", params)
    return [_appt_from_row(r) for r in rows]


# ---------- users ----------

async def upsert_user(phone: str, name: str) -> None:
    async with connect() as db:
        await db.execute(
            """INSERT INTO users (phone, name, created_at) VALUES (?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET name = excluded.name""",
            (phone, name, utcnow_iso()),
        )


async def get_user(phone: str) -> Optional[Dict[str, Any]]:
    async with connect() as db:
        return await db.fetchone("SELECT * FROM users WHERE phone = ?", (phone,))


# ---------- sessions ----------

@dataclass
class Session:
    phone: str
    state: str
    data: Dict[str, Any]


async def get_session(phone: str) -> Optional[Session]:
    async with connect() as db:
        row = await db.fetchone("SELECT * FROM sessions WHERE phone = ?", (phone,))
    if not row:
        return None
    raw = row["data"]
    return Session(
        phone=row["phone"],
        state=row["state"],
        data=json.loads(raw) if isinstance(raw, str) else (raw or {}),
    )


async def set_session(phone: str, state: str, data: Dict[str, Any]) -> None:
    async with connect() as db:
        await db.execute(
            """INSERT INTO sessions (phone, state, data, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(phone) DO UPDATE
                 SET state = excluded.state,
                     data = excluded.data,
                     updated_at = excluded.updated_at""",
            (phone, state, json.dumps(data), utcnow_iso()),
        )


async def delete_session(phone: str) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM sessions WHERE phone = ?", (phone,))

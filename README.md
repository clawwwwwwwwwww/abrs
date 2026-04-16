# ABRS — Appointment Booking & Reminder System

WhatsApp-driven appointment booking with Google Calendar, a 15-minute reminder cron, no-show detection, and a tiny owner admin interface. Designed to be **built and tested for $0** — every external dependency runs in a stub mode locally so you never burn API quota during development.

## Architecture (WAT)

- **Workflows** (`workflows/`): markdown SOPs describing each behavior.
- **Agents** (`app/`): FastAPI handlers + APScheduler jobs that orchestrate.
- **Tools** (`tools/`): deterministic Python — WhatsApp Cloud API client, Google Calendar adapter, slot math, config loader.

## Quick start (stub mode, fully offline)

```bash
pip3 install --user -r requirements.txt
python3 -m pytest                       # 39 tests across all stages
python3 scripts/demo_e2e.py             # full booking + reminders + admin demo, $0
```

Run the server locally and poke it with synthetic webhooks:

```bash
STUB_MODE=1 ./scripts/start.sh          # uvicorn on :8000, sqlite in .tmp/app.db
python3 scripts/fake_inbound.py book    # send "Book" from a fake number
# inspect .tmp/outbox.jsonl for outbound messages
```

## Stub vs real mode

| | Stub (`STUB_MODE=1`, default) | Real (`STUB_MODE=0`) |
|---|---|---|
| Outbound WhatsApp | append to `.tmp/outbox.jsonl` | POST `graph.facebook.com/v20.0/{phone_id}/messages` |
| Inbound HMAC verify | always pass | verify `X-Hub-Signature-256` against `WHATSAPP_APP_SECRET` |
| Google Calendar | read/write `.tmp/calendar.json` | service-account `googleapis.com/calendar/v3` |
| Cost | $0 | within Meta + Google free tiers for personal/test use |

## Environment variables

See [.env.example](./.env.example). Required at runtime:

```
DATABASE_URL=sqlite:///.tmp/app.db
BUSINESS_CONFIG_PATH=config/business.yaml
TZ=America/Los_Angeles
OWNER_WHATSAPP=+14155551234
STUB_MODE=1
```

For real-API smoke (only when ready to demo end-to-end with Meta):

```
WHATSAPP_TOKEN=...                       # Meta Cloud API token
WHATSAPP_PHONE_NUMBER_ID=...             # required for sending
WHATSAPP_VERIFY_TOKEN=...                # for GET /webhook handshake
WHATSAPP_APP_SECRET=...                  # for inbound HMAC
GOOGLE_CALENDAR_ID=...
GOOGLE_CREDS={"type":"service_account",...}   # full JSON, single line
```

> The original brief listed only `WHATSAPP_TOKEN`. Meta's Cloud API actually needs the three additional vars above (`PHONE_NUMBER_ID`, `VERIFY_TOKEN`, `APP_SECRET`).

## Booking flow (state machine)

```
text "Book"            → service list      [AWAITING_SERVICE]
list_reply service:X   → slot list page 0  [AWAITING_SLOT]
list_reply slot:<ISO>  → name prompt OR confirm  [AWAITING_NAME or done]
text <name>            → confirm (creates GCal event + DB row)
text "cancel"          → drop session
```

Stored in the `sessions` table; cleared after confirmation.

## Reminder engine

`app/reminders.py:tick` runs every 15 minutes via APScheduler:

| Job | Window | Action |
|---|---|---|
| 24h reminder | start ∈ now+23h45m..+24h15m | Confirm/Reschedule buttons |
| 2h reminder  | start ∈ now+1h45m..+2h15m   | location + parking + access |
| Follow-up    | end ∈ now-1h15m..-45m       | "How was your visit?" + review link → `completed` |
| No-show      | start < now-1h, 2h sent      | flip to `no_show`, notify owner |

Idempotent: re-running the tick is a no-op (boolean flags + status transitions).

## Admin

Owner-only WhatsApp commands (case-insensitive):
- `Today`, `Tomorrow` — list appointments
- `Stats` — bookings / completed / no-shows / cancelled / completion rate for this week

## Schema

[migrations/001_init.sql](migrations/001_init.sql) — SQLite (dev default).
[migrations/001_init.postgres.sql](migrations/001_init.postgres.sql) — Postgres flavor (deploy day swap, no code change).

## Tests

```bash
python3 -m pytest -v
```

| File | What it covers |
|---|---|
| [tests/test_config.py](tests/test_config.py) | YAML loader |
| [tests/test_slots.py](tests/test_slots.py) | slot math (business hours, busy intervals, grid, paging) |
| [tests/test_db.py](tests/test_db.py) | migrations + appointment/user/session CRUD |
| [tests/test_whatsapp.py](tests/test_whatsapp.py) | inbound parsing, outbound stub, HMAC verify |
| [tests/test_gcal.py](tests/test_gcal.py) | stub calendar create/list/cancel |
| [tests/test_booking_flow.py](tests/test_booking_flow.py) | end-to-end booking conversation |
| [tests/test_reminders.py](tests/test_reminders.py) | all four reminder branches + idempotency |
| [tests/test_admin.py](tests/test_admin.py) | Today/Tomorrow/Stats + owner guard |

## Going to production (real APIs)

Stage 9 — only when you want a real-WhatsApp smoke:

1. Meta dev account → WhatsApp → use the free test number; copy `WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`, set a `VERIFY_TOKEN`, copy the `APP_SECRET`.
2. Google Cloud Console → create a project (free) → enable Calendar API → make a service account → download JSON; share your calendar with the service-account email.
3. Local tunnel (free, no signup): `brew install cloudflared && cloudflared tunnel --url http://localhost:8000`. Use the printed URL as the Meta webhook callback (`/webhook`).
4. Set `STUB_MODE=0` and the seven extra env vars; restart uvicorn. Send "Book" from your phone — message arrives, slots come back, calendar event appears.

## Going to production (hosting)

Stage 10 — deploy is intentionally deferred so you don't pay until the bot works.

- **Cheapest path**: Render free web service (sleeps after 15 min idle — fine for a demo, not for live reminders) + Neon free Postgres.
- **Always-on**: Railway $5/month + Postgres plugin. Apply `001_init.postgres.sql` once. Set the same env vars.
- Procfile / Railway start command: `bash scripts/start.sh`.

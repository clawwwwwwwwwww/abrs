# Workflow: Booking Flow

## Objective
Take a customer from "Book" keyword to a confirmed Google Calendar event + DB row + WhatsApp confirmation, in 3–4 messages.

## Inputs
- Inbound WhatsApp message from a customer (via Meta webhook → `app/main.py:webhook_event`).
- `BUSINESS_CONFIG_PATH` YAML: services with durations, weekly hours, location.
- Existing busy intervals on `GOOGLE_CALENDAR_ID` for the next 7 days.

## Tools (in order)
1. `tools/whatsapp.py:parse_incoming` — normalize Meta payload.
2. `app/router.py:route` — dispatch: admin? button? booking?
3. `app/booking.py:handle` — state machine.
4. `tools/gcal.py:list_busy(now, now+8d)` — fetch busy intervals.
5. `tools/slots.py:compute_available_slots` — produce next-7-day slots on a 15-min grid.
6. `tools/whatsapp.py:send_list` — service list, slot list, "More times…".
7. `tools/whatsapp.py:send_text` — name prompt, confirmation.
8. `tools/gcal.py:create_event` — write to calendar.
9. `app/db.py:create_appointment` — write the booking row.

## Outputs
- `appointments` row with `status='booked'` and `calendar_event_id`.
- New `users` row if first time (phone → name).
- Google Calendar event in the configured calendar.
- WhatsApp confirmation message containing booking id, service, local time, address.

## State machine
Sessions live in the `sessions` table keyed by phone.

| State | Trigger | Action |
|---|---|---|
| (none) | text matches `^(book|appointment|schedule)$` | send service list → `AWAITING_SERVICE` |
| `AWAITING_SERVICE` | list_reply id = `service:<name>` | resolve service, fetch slots, send page 0 → `AWAITING_SLOT` |
| `AWAITING_SLOT` | list_reply id = `slot:<ISO>` | store slot; if returning user → confirm; else ask name → `AWAITING_NAME` |
| `AWAITING_SLOT` | list_reply id = `more_slots` | send next page (same state) |
| `AWAITING_NAME` | text | upsert user → confirm |
| any | text matches `^cancel$` | drop session, ack |

## Edge cases / learnings
- WhatsApp interactive list: max 10 rows per section; we cap at 9 + a "More times…" row.
- Slot ids carry the ISO8601 UTC timestamp so we don't have to look them up server-side.
- Title fields are 24 chars max, descriptions 72; we slice defensively in `tools/whatsapp.py`.
- "No slots in 7 days" → drop the session and tell the user; don't loop.
- Time-zone display uses `TZ` env var; storage is always ISO8601 UTC strings (works on SQLite + Postgres unchanged).

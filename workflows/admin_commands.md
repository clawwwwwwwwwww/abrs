# Workflow: Admin Commands

## Objective
Give the owner a tiny WhatsApp UI to inspect today's/tomorrow's bookings and weekly stats.

## Inputs
- Inbound text from `OWNER_WHATSAPP` (string match on phone in `app/router.py`).
- `app/db.py:query_appointments` for the relevant time windows.

## Tools (in order)
1. `app/router.py:route` — guards `from_phone == OWNER_WHATSAPP`.
2. `app/admin.py:handle` — case-insensitive command parser.
3. `app/db.py:query_appointments` — day-bounded or week-bounded query.
4. `tools/whatsapp.py:send_text` — formatted reply.

## Commands
| Command | Action |
|---|---|
| `Today` | List today's appointments (local TZ), ordered by time, with status. |
| `Tomorrow` | Same, +1 day. |
| `Stats` | Current ISO week (Mon–Sun, local TZ): bookings, completed, no-shows, cancelled, completion rate. |

## Stats formula
```
completion_rate = completed / (completed + no_show + cancelled) * 100
```
Active/future bookings are excluded from the denominator so the rate isn't dragged down by appointments that haven't happened yet.

## Edge cases
- Non-owner senders never see admin output; they fall through to the "Send 'Book' to schedule" help text.
- Empty days respond with `"No appointments today/tomorrow."`.
- `Stats` with no terminal appointments shows `Completion rate: 0%` (denominator 0 is treated as 0%).

# Workflow: Reminder Engine

## Objective
Send the right reminder at the right moment for every appointment, exactly once. Run as a 15-minute APScheduler tick.

## Inputs
- Appointments table state at tick time.
- `tools/config.py` — location/parking/access notes/review URL.
- `OWNER_WHATSAPP` env var — destination for no-show notices.

## Tools (in order)
1. `app/db.py:query_appointments` — four narrow time-window queries.
2. `tools/whatsapp.py:send_buttons` — Confirm/Reschedule + review.
3. `tools/whatsapp.py:send_text` — 2h note, follow-up review link, owner notice.
4. `app/db.py:mark_flag` / `update_appointment_status` — record what was sent.

## Outputs
Per appointment:
- 24h reminder with two buttons; sets `reminder_24h_sent=1`.
- 2h reminder text with location bundle; sets `reminder_2h_sent=1`.
- Follow-up message with review link; sets `followup_sent=1` and `status='completed'`.
- No-show: `status='no_show'` and a text to owner.

## Time windows (all UTC)
| Job | Window | Status filter | Flag guard |
|---|---|---|---|
| 24h reminder | `now+23h45m` ≤ datetime ≤ `now+24h15m` | `status='booked'` | `reminder_24h_sent=0` |
| 2h reminder  | `now+1h45m` ≤ datetime ≤ `now+2h15m`   | `status IN ('booked','confirmed')` | `reminder_2h_sent=0` |
| Follow-up    | end-time in `[now-1h15m, now-45m]` | `status IN ('booked','confirmed')` | `followup_sent=0` |
| No-show      | datetime < `now-1h` | `status='booked'` AND `reminder_2h_sent=1` | (status flip is the guard) |

Why the windows: a tick happens every 15 min, so a 30-min window centered on the target time guarantees coverage even if a tick is delayed. Boolean flags + status transitions stop double-sends.

## Edge cases / learnings
- Confirm button → `status='confirmed'` (still gets the 2h reminder).
- Reschedule button → cancel GCal event, `status='cancelled'`, restart booking flow.
- Follow-up only fires for appointments that had `status` still in `{booked,confirmed}` — once we mark `completed`, it won't repeat.
- A no-show check requires `reminder_2h_sent=1` so we don't punish customers who never got the 2h nudge (e.g., booking placed within 2h of start).
- All idempotent: re-running `tick` immediately is a no-op (verified in `tests/test_reminders.py:test_tick_is_idempotent`).

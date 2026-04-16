# Workflow: No-Show Handling

## Objective
Detect customers who didn't show up and notify the owner so they can act (charge a fee, follow up, etc.).

## Inputs
- Appointment with `status='booked'`, `reminder_2h_sent=1`, `datetime < now - 1h`.
- `OWNER_WHATSAPP` env var.

## Tools
1. `app/db.py:query_appointments` — pull candidates.
2. `app/db.py:update_appointment_status(id, 'no_show')`.
3. `tools/whatsapp.py:send_text(owner, …)` — owner notification.

## Owner notification format
```
No-show: <name> (<phone>) — <service> at <local time>.
```

## Decision rationale
- **`reminder_2h_sent=1` requirement**: never flag a no-show for an appointment we never reminded. Protects last-minute bookings made <2h ahead.
- **`datetime < now-1h`**: gives the customer a 1h grace window; covers transit, traffic, etc.
- **Status guard**: only `booked` flips to `no_show`. If the customer `Confirmed` we don't punish them; if they `cancelled` it's already terminal.

## Ops note
Owner notices are part of the same outbox stream; in stub mode they appear in `.tmp/outbox.jsonl` keyed to `OWNER_WHATSAPP`.

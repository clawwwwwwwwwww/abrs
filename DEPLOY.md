# Deploy ABRS to production for $0/month

Stack: **Render** (free web service) + **Neon** (free Postgres) + **cron-job.org** (free 15-min cron) + **Meta WhatsApp Cloud API** (free test number) + **Google Calendar API** (free).

No credit card required for any of these.

---

## 0. What you need before starting

- A phone number you can receive WhatsApp messages on (for the Meta test number)
- Email accounts for: GitHub, Render, Neon, cron-job.org, Meta for Developers, Google Cloud
- ~45 minutes

---

## 1. Push the code to GitHub

```bash
cd /Users/shreyashgupta/ABRS
git init
git add .
git commit -m "Initial ABRS commit"
```

Create an empty repo at https://github.com/new (name it `abrs`, leave it empty), then:

```bash
git remote add origin https://github.com/<your-username>/abrs.git
git branch -M main
git push -u origin main
```

---

## 2. Provision a free Postgres on Neon

1. Sign up at https://neon.tech (free, no card).
2. Create a project. Choose the closest region.
3. After the project loads, copy the **"Pooled connection"** string from the Connection Details panel. It looks like:
   ```
   postgresql://user:pass@ep-xxxx-pooler.region.aws.neon.tech/neondb?sslmode=require
   ```
4. Save this somewhere — you'll paste it into Render in step 4.

> Use the **pooled** URL, not the direct one — Neon's free tier benefits from the pooler.

---

## 3. Set up the Meta WhatsApp Cloud API

1. Go to https://developers.facebook.com → **My Apps** → **Create App**.
2. Use case: **Other** → Type: **Business** → name it `ABRS`.
3. In the new app, click **Add Product** → **WhatsApp** → **Set up**.
4. Inside WhatsApp → **API Setup**:
   - Copy the **Temporary access token** (24h; we'll swap to permanent later).
   - Copy the **Phone number ID** (a long number under "From").
   - Add your personal WhatsApp number under **To** ("recipient number"). Confirm via the OTP.
5. Inside WhatsApp → **Configuration** → **Webhook**:
   - **Verify token**: invent any string, e.g. `letmein-9f3a`. Save it.
   - Leave the **Callback URL** blank for now (we'll fill it in after Render gives us a URL).
6. App settings → **Basic** → reveal and copy the **App secret**.

You now have these four values (write them down):
- `WHATSAPP_TOKEN` (temporary token from step 4)
- `WHATSAPP_PHONE_NUMBER_ID` (from step 4)
- `WHATSAPP_VERIFY_TOKEN` (the string you invented in step 5)
- `WHATSAPP_APP_SECRET` (from step 6)

---

## 4. Deploy on Render

1. Sign up at https://render.com with the same GitHub account.
2. Dashboard → **New** → **Blueprint**.
3. Connect your `abrs` GitHub repo. Render reads `render.yaml` and offers to create the `abrs` web service. Click **Apply**.
4. While it's building, click into the new service → **Environment** and add (one at a time):

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Neon pooled URL from step 2 |
   | `STUB_MODE` | `0` |
   | `DISABLE_SCHEDULER` | `1` |
   | `CRON_SECRET` | run `python3 -c "import secrets; print(secrets.token_hex(24))"` and paste the output |
   | `OWNER_WHATSAPP` | your WhatsApp number in E.164, e.g. `+14155551234` |
   | `WHATSAPP_TOKEN` | from step 3 |
   | `WHATSAPP_PHONE_NUMBER_ID` | from step 3 |
   | `WHATSAPP_VERIFY_TOKEN` | from step 3 |
   | `WHATSAPP_APP_SECRET` | from step 3 |
   | `GOOGLE_CALENDAR_ID` | filled in step 5 below |
   | `GOOGLE_CREDS` | filled in step 5 below |

5. Click **Save Changes**. Render redeploys.
6. Once status is **Live**, copy the public URL (e.g. `https://abrs-xxxx.onrender.com`).

---

## 5. Set up Google Calendar (service account)

1. https://console.cloud.google.com → create a project (free).
2. **APIs & Services** → **Library** → enable **Google Calendar API**.
3. **APIs & Services** → **Credentials** → **Create credentials** → **Service account**:
   - Name: `abrs-bot`. No roles needed. Skip user-grant step.
4. Open the new service account → **Keys** → **Add key** → **JSON**. A file downloads — keep it safe.
5. Find the service account's email (looks like `abrs-bot@<project>.iam.gserviceaccount.com`).
6. Open https://calendar.google.com → settings of the calendar you want bookings on → **Share with specific people** → add the service account email with **Make changes to events** permission.
7. Same calendar settings → scroll to **Integrate calendar** → copy the **Calendar ID** (an email-like string).

In Render, set:
- `GOOGLE_CALENDAR_ID` = the calendar id from step 7
- `GOOGLE_CREDS` = the **entire JSON file contents on a single line**. macOS shortcut:
  ```bash
  cat ~/Downloads/abrs-bot-XXXX.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))" | pbcopy
  ```
  Then paste into the Render value field.

---

## 6. Wire the Meta webhook to your Render URL

Back in Meta → WhatsApp → **Configuration** → **Webhook**:
- **Callback URL**: `https://abrs-xxxx.onrender.com/webhook`
- **Verify token**: the same value you set in `WHATSAPP_VERIFY_TOKEN` on Render
- Click **Verify and save** — should turn green
- Subscribe to the **`messages`** field

---

## 7. Schedule the reminder cron at cron-job.org

1. Sign up at https://cron-job.org (free, no card).
2. **Cronjobs** → **Create cronjob**.

   **Job 1 — keep-awake (so Render free tier doesn't sleep):**
   - Title: `abrs keep-awake`
   - URL: `https://abrs-xxxx.onrender.com/health`
   - Schedule: every 10 minutes
   - Save.

   **Job 2 — reminder tick:**
   - Title: `abrs reminders`
   - URL: `https://abrs-xxxx.onrender.com/cron/tick`
   - Method: **POST**
   - Headers → add `X-Cron-Secret: <paste the same value as CRON_SECRET>`
   - Schedule: every 15 minutes
   - Save.

---

## 8. Smoke test from your phone

1. From the WhatsApp number you registered as a test recipient, send `Book` to the Meta-provided test number.
2. You should get back a service list within a couple of seconds.
3. Pick a service → pick a slot → enter your name → confirmation.
4. Open Google Calendar — the event should be there.
5. From the same number that's set as `OWNER_WHATSAPP`, send `Today` — you should see your booking.

If something doesn't fire, check Render → Logs.

---

## 9. After 24 hours: swap the temporary WhatsApp token

Meta's "Temporary access token" expires every 24h. Replace it with a permanent System User token:

1. https://business.facebook.com → **Settings** → **Users** → **System Users** → add a system user with **Admin**.
2. Generate a token for that user with `whatsapp_business_messaging` and `whatsapp_business_management` permissions, **no expiry**.
3. Update `WHATSAPP_TOKEN` in Render with the new value. Render redeploys automatically.

---

## What stays free vs. what eventually costs money

| Thing | Free until |
|---|---|
| Render web service | Forever (sleeps after 15m idle — keep-awake cron handles it) |
| Neon Postgres | Forever for our data volume (free plan: 0.5 GB storage) |
| cron-job.org | Forever |
| Meta WhatsApp Cloud API | First 1,000 service conversations/month free — well above test-volume |
| Google Calendar API | Forever for our request volume |

When you outgrow the free tier (rare for a single business): upgrade Render to Starter ($7/mo) for always-on + zero cold starts.

---

## Local development still works exactly the same

Everything in the README's "Quick start" section keeps working:

```bash
STUB_MODE=1 ./scripts/start.sh
python3 -m pytest
python3 scripts/demo_e2e.py
```

The dual-backend `app/db.py` picks SQLite when `DATABASE_URL` is `sqlite://...` (default) and Postgres when it's `postgres://...`.

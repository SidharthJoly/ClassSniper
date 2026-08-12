# gym-bot

Automates gym class booking. Classes open for booking 72 hours ahead; this repo
queues a target class in `pending_booking.json` and a scheduled GitHub Actions
workflow (`gym_script.py`, every 15 minutes) strikes the instant that window opens.

Live web UI: https://sidharthjoly.github.io/gym-bot/

## Safety rule

Cancelling inside 24 hours of a class incurs a charge, so the bot refuses to
strike (or let you arm) any class starting less than **30 hours** from now.
This is enforced both in the web UI and, authoritatively, in `gym_script.py`
itself (`BLOCKED_TOO_CLOSE` status).

## Web UI

Open `index.html` (or the GitHub Pages link above):

- **Status** and **Pending Bookings** are public reads — no GitHub token needed.
- Arming or removing a booking needs a GitHub token, since that writes to the repo.
  Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new)
  scoped to just this repo with **Contents: read & write**, paste it into
  "GitHub Connection" once — it's stored only in that browser's `localStorage`.

## How the bot decides what to strike

`pending_booking.json` holds an array of `{date, time}` targets. Each run picks
whichever eligible target's 72-hour booking window opens soonest, waits for it
if needed, then attempts the strike. On success, that target is automatically
removed from the queue.

## Manually arming via GitHub Actions

The workflow also accepts a manual `workflow_dispatch` trigger with
`class_date` / `class_time` inputs (Actions tab → "Run workflow"), which
appends to the pending queue the same way the web UI does.

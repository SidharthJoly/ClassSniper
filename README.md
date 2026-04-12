# gym-bot

This repo automates gym booking by storing pending classes in `pending_booking.json` and using a scheduled workflow with `gym_script.py`.

## Web UI

Open `index.html` in a browser and:

- set your GitHub username, repo, and token
- refresh the scraped class list
- select a class to add it to pending bookings
- pending bookings are stored in `pending_booking.json`

The script now supports an array of pending bookings and will book the earliest pending class when its reservation window opens.

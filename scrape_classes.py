import json
from datetime import datetime, timedelta

import pytz
import requests

# Newtown. See https://cms.oneplayground.com.au/api/timetable/centers for other locations.
CENTER_ID = 104
CENTER_NAME = "Newtown"
LOOKAHEAD_DAYS = 10
API_URL = "https://cms.oneplayground.com.au/api/timetable/get-sessions-by-center-and-date"

# Matches gym_script.py's MIN_LEAD_HOURS: cancelling inside 24h incurs a fee, so
# don't even offer a class that couldn't be armed anyway.
MIN_LEAD_HOURS = 30


def scrape():
    sydney_tz = pytz.timezone("Australia/Sydney")
    now = datetime.now(sydney_tz)
    today = now.date()
    from_date = today
    to_date = today + timedelta(days=LOOKAHEAD_DAYS + 1)  # API's to_date is exclusive

    response = requests.post(
        API_URL,
        json={
            "center_id": CENTER_ID,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        },
        timeout=20,
    )
    response.raise_for_status()
    sessions = response.json().get("sessions", [])

    min_start = now.replace(tzinfo=None) + timedelta(hours=MIN_LEAD_HOURS)

    classes = []
    for s in sessions:
        if s.get("booking_state") != "ACTIVE":
            continue
        start = datetime.strptime(s["booking_start_datetime"], "%Y-%m-%d %H:%M:%S")
        if start < min_start:
            continue
        classes.append({
            "booking_id": s["booking_id"],
            "date": start.strftime("%Y-%m-%d"),
            "time": start.strftime("%-I:%M %p"),
            "name": s.get("booking_name") or s.get("activity_name"),
            "instructor": s.get("instructors"),
            "location": s.get("center_name", CENTER_NAME),
            "remaining_spots": s.get("remaining_spots"),
            "capacity": s.get("class_capacity"),
        })

    classes.sort(key=lambda c: (c["date"], datetime.strptime(c["time"], "%I:%M %p")))

    with open("class_list.json", "w") as f:
        json.dump(classes, f, indent=2)

    print(f"Wrote {len(classes)} armable classes to class_list.json")


if __name__ == "__main__":
    scrape()

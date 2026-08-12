import os
import re
import json
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import pytz

# --- CONFIG FROM SECRETS ---
EMAIL = os.getenv("GYM_EMAIL")
PASSWORD = os.getenv("GYM_PASSWORD")

TIMETABLE_URL = "https://oneplayground.com.au/classes/timetable/"
DEFAULT_LOCATION = "Newtown"

# Matches both "6:00 AM" (pending_booking.json) and the site's own compact
# "6AM" / "6.30AM" class-list format, so the two can be compared directly.
TIME_RE = re.compile(r'^(\d{1,2})(?:[.:](\d{2}))?\s*([AaPp][Mm])$')


def parse_class_time(text):
    m = TIME_RE.match(text.strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3).upper()
    return (hour, minute, ampm)


async def select_location(page, location_name):
    await page.click('[data-testid="location-filter"]')
    await page.get_by_role("button", name=location_name, exact=True).click()
    await page.wait_for_selector('[data-testid="day-tab"]', timeout=15000)


async def select_day(page, target_date):
    """Click the day-tab matching target_date, paging forward through weeks if needed."""
    target_day_num = str(target_date.day)
    expected_header = target_date.strftime("%A, %b %-d").upper()

    for _ in range(8):  # generous cap; target is normally within the first window
        tabs = page.locator('[data-testid="day-tab"]')
        count = await tabs.count()
        for i in range(count):
            tab = tabs.nth(i)
            if await tab.is_disabled():
                continue
            text = (await tab.inner_text()).strip()
            day_num = text.splitlines()[-1].strip()
            if day_num == target_day_num:
                await tab.click()
                await page.wait_for_timeout(700)
                if await page.locator("h3", has_text=expected_header).count() > 0:
                    return True
                break  # day-number matched but wrong month/header; keep paging
        next_btn = page.locator('button[aria-label="Next week"]')
        if await next_btn.is_disabled():
            break
        await next_btn.click()
        await page.wait_for_timeout(700)
    return False


async def select_ampm(page, ampm):
    """The AM/PM toggle actually filters which classes render — defaults to AM only."""
    await page.get_by_role("button", name=ampm, exact=True).click()
    await page.wait_for_timeout(500)


async def ensure_logged_in(page):
    """The site only prompts for login when it's actually needed (e.g. on Book click)."""
    email_input = page.locator('input[type="email"]')
    if await email_input.is_visible():
        await email_input.fill(EMAIL)
        await page.locator('input[type="password"]').fill(PASSWORD)
        await page.click('[data-testid="login-submit"]')
        await email_input.wait_for(state="hidden", timeout=15000)


async def find_and_click_book(page, time_text):
    """Returns (status, detail) where status is one of CLICKED / FULL / NOT_FOUND."""
    target_parsed = parse_class_time(time_text)
    rows = page.locator('[data-testid="class-row"]')
    count = await rows.count()
    for i in range(count):
        row = rows.nth(i)
        time_el = row.locator('[data-testid="session-start-time"]')
        if await time_el.count() == 0:
            continue
        row_parsed = parse_class_time((await time_el.inner_text()).strip())
        if row_parsed != target_parsed:
            continue
        # The site renders both a desktop and mobile layout for each row (only one
        # actually visible at a time), each with its own book-btn — scope to the
        # visible one to avoid a strict-mode "resolved to 2 elements" error.
        book_btn = row.locator('[data-testid="book-btn"]:visible')
        if await book_btn.count() == 0:
            return "NOT_FOUND", "Matched the class row but found no visible book button."
        if not await book_btn.is_enabled():
            btn_text = (await book_btn.inner_text()).strip()
            return "FULL", f"Class is full ({btn_text})."
        await book_btn.click()
        return "CLICKED", None
    return "NOT_FOUND", f"No class row found for time {time_text}."


async def _run_strike(page, target, location_name, result):
    """Drives the actual browser interaction. Mutates `result` in place; raises on
    any unexpected error so the caller can screenshot before the driver tears down."""
    print(f"Opening timetable and selecting {location_name}...")
    await page.goto(TIMETABLE_URL, wait_until="networkidle", timeout=30000)
    await page.wait_for_selector('[data-testid="location-filter"]', timeout=15000)
    await select_location(page, location_name)

    print(f"Selecting {target['date']}...")
    target_date_obj = datetime.strptime(target['date'], "%Y-%m-%d")
    if not await select_day(page, target_date_obj):
        await page.screenshot(path="final_failure.png")
        result.update({
            "status": "NOT_FOUND",
            "time": str(datetime.now()),
            "error": f"Could not find {target['date']} in the day picker.",
        })
        return

    target_parsed_time = parse_class_time(target['time'])
    if target_parsed_time is None:
        result.update({
            "status": "ERROR",
            "time": str(datetime.now()),
            "error": f"Could not parse time '{target['time']}' (expected e.g. '6:00 AM').",
        })
        return
    await select_ampm(page, target_parsed_time[2])

    book_status = None
    book_detail = None
    for attempt in range(1, 6):
        book_status, book_detail = await find_and_click_book(page, target['time'])
        if book_status == "CLICKED":
            print(f"Attempt {attempt}: found {target['time']} and clicked Book.")
            break
        print(f"Attempt {attempt}: {book_detail}")
        if book_status == "FULL":
            break
        await asyncio.sleep(4)

    if book_status == "FULL":
        result.update({
            "status": "FAILED",
            "time": str(datetime.now()),
            "error": book_detail,
        })
    elif book_status != "CLICKED":
        await page.screenshot(path="final_failure.png")
        result.update({
            "status": "NOT_FOUND",
            "time": str(datetime.now()),
            "error": book_detail,
        })
    else:
        # Site only asks to sign in once you try to actually book.
        await page.wait_for_timeout(1000)
        await ensure_logged_in(page)
        await page.wait_for_timeout(1500)

        confirm_btn = page.get_by_role("button", name=re.compile("confirm", re.I))
        if await confirm_btn.count() > 0:
            await confirm_btn.first.click()
            await page.wait_for_timeout(1000)

        # The post-login/confirm UI hasn't been verified end-to-end against a real
        # account yet, so capture a screenshot every time and flag it for review
        # rather than assuming the booking definitely went through.
        await page.screenshot(path="booking_result.png")
        result.update({
            "status": "SUCCESS",
            "time": str(datetime.now()),
            "note": "Clicked Book and completed the sign-in/confirm flow — verify against booking_result.png artifact until this path is confirmed reliable.",
        })


async def run_booking():
    # 1. Load your pending bookings from the repository
    try:
        with open('pending_booking.json', 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("No pending_booking.json found. Create one to start.")
        return

    if isinstance(data, dict):
        targets = [data]
    elif isinstance(data, list):
        targets = data
    else:
        raise ValueError("pending_booking.json must contain an object or an array of objects")

    # Define Sydney Timezone
    sydney_tz = pytz.timezone('Australia/Sydney')

    # Cancelling inside 24h of a class incurs a charge. Never strike a class that
    # starts less than this many hours from now, so it can always be safely cancelled.
    MIN_LEAD_HOURS = 30

    now = datetime.now(sydney_tz)

    bookings = []
    for target in targets:
        target_dt = sydney_tz.localize(datetime.strptime(f"{target['date']} {target['time']}", "%Y-%m-%d %I:%M %p"))
        bookings.append({
            "target": target,
            "target_dt": target_dt,
            "opens_at": target_dt - timedelta(hours=72),
        })

    if not bookings:
        print("No pending bookings queued. Nothing to do.")
        return

    eligible = [b for b in bookings if (b["target_dt"] - now) >= timedelta(hours=MIN_LEAD_HOURS)]
    for b in bookings:
        if b not in eligible:
            print(
                f"Skipping {b['target']}: class starts in {b['target_dt'] - now}, "
                f"under the {MIN_LEAD_HOURS}h cancellation-safety margin."
            )

    if not eligible:
        result = {
            "status": "BLOCKED_TOO_CLOSE",
            "time": str(datetime.now()),
            "error": f"All pending bookings start within {MIN_LEAD_HOURS}h; skipped to avoid a cancellation-fee risk.",
        }
        with open('status.json', 'w') as f:
            json.dump(result, f)
        return

    eligible.sort(key=lambda entry: entry["opens_at"])
    selected = eligible[0]
    target = selected["target"]
    booking_opens_at = selected["opens_at"]

    window = timedelta(minutes=15)

    if now < booking_opens_at - window:
        print(
            f"Too early for the next pending booking. Earliest open time is {booking_opens_at}, current Sydney time is {now}."
        )
        return

    if now < booking_opens_at:
        wait_seconds = (booking_opens_at - now).total_seconds() + 2
        print(
            f"Booking opens at {booking_opens_at}. Waiting {int(wait_seconds)} seconds to strike at {booking_opens_at + timedelta(seconds=2)}."
        )
        await asyncio.sleep(wait_seconds)
        now = datetime.now(sydney_tz)
    elif now < booking_opens_at + timedelta(seconds=2):
        sleep_seconds = (booking_opens_at + timedelta(seconds=2) - now).total_seconds()
        print(f"Booking is opening now. Sleeping {int(sleep_seconds)} seconds to hit the target window.")
        await asyncio.sleep(sleep_seconds)
        now = datetime.now(sydney_tz)

    result = {
        "status": "PENDING",
        "time": str(datetime.now()),
        "selected_target": target,
        "selected_open_time": str(booking_opens_at),
    }

    location_name = target.get("location", DEFAULT_LOCATION)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            try:
                await _run_strike(page, target, location_name, result)
            except Exception:
                # Screenshot here, while the Playwright driver is still alive — taking
                # it from the outer except (after `async with` has torn down) silently
                # fails, which is exactly the bug that hid earlier production failures.
                try:
                    await page.screenshot(path="final_failure.png")
                except Exception:
                    pass
                raise
    except Exception as e:
        print(f"Strike failed: {e}")
        result.update({
            "status": "ERROR",
            "time": str(datetime.now()),
            "error": str(e),
        })
    finally:
        if result.get("status") == "SUCCESS":
            try:
                targets.remove(target)
            except ValueError:
                pass
            with open('pending_booking.json', 'w') as f:
                json.dump(targets, f, indent=2)

        with open('status.json', 'w') as f:
            json.dump(result, f)

        if 'browser' in locals():
            try:
                await browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(run_booking())

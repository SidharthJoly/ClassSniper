import os
import json
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import pytz

# --- CONFIG FROM SECRETS ---
EMAIL = os.getenv("GYM_EMAIL")
PASSWORD = os.getenv("GYM_PASSWORD")

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

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # LOGIN
            print("Logging in...")
            await page.goto("https://oneplayground.exerp.site/booking?centers=104")

            # Instead of wait_for_load_state("networkidle"), wait for a specific element
            await page.wait_for_selector("input[type='email']", timeout=15000)

            await page.get_by_label("Email").fill(EMAIL)
            await page.get_by_label("Password").fill(PASSWORD)
            await page.get_by_role("button", name="Sign in").click()

            # Wait for the URL to change or the 'My Bookings' text to appear
            # instead of waiting for the whole network to be idle
            await page.wait_for_url("**/booking**", timeout=20000)
            print("Login successful (or redirected).")

            print(f"Navigating to {target['date']}...")
            await page.goto(f"https://oneplayground.exerp.site/booking?centers=104&date={target['date']}")

            try:
                await page.wait_for_selector("text=Newtown", timeout=20000)
                print("Newtown data loaded on page.")
            except Exception:
                print("Timed out waiting for 'Newtown' text.")

            found_classes = False
            for attempt in range(1, 6):
                target_slot = page.get_by_text(target['time'], exact=False).first

                if await target_slot.is_visible():
                    print(f"Attempt {attempt}: Found {target['time']} slot!")
                    found_classes = True
                    await target_slot.click()
                    break
                else:
                    print(f"Attempt {attempt}: {target['time']} not visible yet...")
                    await asyncio.sleep(4)

            if not found_classes:
                print("CRITICAL: Still can't find class. Trying one last 'Force' selector...")
                try:
                    await page.locator(f"xpath=//*[contains(text(), '{target['time']}')]" ).first.click()
                    found_classes = True
                except Exception as e:
                    print(f"Force selector failed: {e}")
                    await page.screenshot(path="final_failure.png")
                    result.update({
                        "status": "NOT_FOUND",
                        "time": str(datetime.now()),
                        "error": f"Could not find class slot for {target['time']}",
                    })
                    return

            if found_classes:
                await asyncio.sleep(2)
                btn = page.get_by_role("button", name="Book").or_(page.get_by_role("button", name="Waitlist")).first

                if await btn.is_visible():
                    print(f"Button found: {await btn.inner_text()}. Striking...")
                    await btn.click()

                    confirm = page.get_by_role("button", name="Confirm", exact=False)
                    await confirm.wait_for(state="visible", timeout=5000)
                    await confirm.click()
                    print("STRIKE COMPLETE.")
                    result.update({
                        "status": "SUCCESS",
                        "time": str(datetime.now()),
                    })
                else:
                    print("Book/Waitlist button never appeared.")
                    result.update({
                        "status": "FAILED",
                        "time": str(datetime.now()),
                        "error": "Book/Waitlist button was not visible",
                    })
    except Exception as e:
        print(f"Strike failed: {e}")
        result.update({
            "status": "ERROR",
            "time": str(datetime.now()),
            "error": str(e),
        })
        if 'page' in locals():
            try:
                await page.screenshot(path="final_failure.png")
            except Exception:
                pass
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

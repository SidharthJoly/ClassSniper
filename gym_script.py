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
    # 1. Load your selection from the repository
    try:
        with open('pending_booking.json', 'r') as f:
            target = json.load(f)
    except FileNotFoundError:
        print("No pending_booking.json found. Create one to start.")
        return

    # Define Sydney Timezone
    sydney_tz = pytz.timezone('Australia/Sydney')
    
    # Get the target date/time as a Sydney-aware object
    target_dt = sydney_tz.localize(datetime.strptime(f"{target['date']} {target['time']}", "%Y-%m-%d %I:%M %p"))
    booking_opens_at = target_dt - timedelta(hours=72)
    
    # Get CURRENT time in Sydney
    now = datetime.now(sydney_tz)

    # Now the comparison will be accurate
    if now < (booking_opens_at - timedelta(minutes=15)):
        print(f"Too early. Opens at {booking_opens_at}. Current Sydney time: {now}")
        return
        
    result = {
        "status": "PENDING",
        "time": str(datetime.now()),
        "target": target,
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
        await page.screenshot(path="final_failure.png")
        result.update({
            "status": "ERROR",
            "time": str(datetime.now()),
            "error": str(e),
        })
    finally:
        with open('status.json', 'w') as f:
            json.dump(result, f)

        if 'browser' in locals():
            await browser.close()

if __name__ == "__main__":
    asyncio.run(run_booking())

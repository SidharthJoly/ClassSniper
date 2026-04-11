import os
import json
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

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

    target_dt = datetime.strptime(f"{target['date']} {target['time']}", "%Y-%m-%d %I:%M %p")
    booking_opens_at = target_dt - timedelta(hours=72)
    now = datetime.now()

    # 2. Precision Wait Logic
    if now < (booking_opens_at - timedelta(minutes=10)):
        print(f"Too early. Opens at {booking_opens_at}. Current time: {now}")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # LOGIN
        print("Logging in...")
        await page.goto("https://oneplayground.exerp.site/booking?centers=104")
        await page.get_by_label("Email").fill(EMAIL)
        await page.get_by_label("Password").fill(PASSWORD)
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_load_state("networkidle")

        # GO TO DATE
        await page.goto(f"https://oneplayground.exerp.site/booking?centers=104&date={target['date']}")
        
        # 3. THE FINAL COUNTDOWN
        while datetime.now() < booking_opens_at:
            await asyncio.sleep(0.5)

        # 4. ACTION PHASE
        try:
            await page.reload()
            # Locate row
            class_row = page.locator(".booking-class-item").filter(has_text=target['time']).first
            
            # Check if it's already full
            row_text = await class_row.inner_text()
            print(f"Row Status: {row_text}")

            # Click to expand
            await class_row.click() 
            
            # Find the button (could be 'Book' or 'Waitlist')
            btn = class_row.locator("button").filter(has_text="Book").or_(
                  class_row.locator("button").filter(has_text="Waitlist")
            ).first
            
            if await btn.is_visible():
                btn_text = await btn.inner_text()
                print(f"Striking button: {btn_text}")
                await btn.click(force=True)
                
                # Modal confirmation
                confirm = page.get_by_role("button", name="Confirm", exact=False)
                await confirm.wait_for(state="visible", timeout=3000)
                await confirm.click()
                
                result = {"status": btn_text.upper(), "time": str(datetime.now())}
            else:
                result = {"status": "FULL/NO BUTTON", "time": str(datetime.now())}

        except Exception as e:
            print(f"Strike failed: {e}")
            result = {"status": "ERROR", "time": str(datetime.now()), "error": str(e)}

        # 5. SAVE RESULT (Updates your Dashboard)
        with open('status.json', 'w') as f:
            json.dump(result, f)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_booking())
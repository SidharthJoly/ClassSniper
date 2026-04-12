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
        # GO TO DATE
        print(f"Navigating to {target['date']}...")
        await page.goto(f"https://oneplayground.exerp.site/booking?centers=104&date={target['date']}")
        
        # Wait for the container first
        try:
            await page.wait_for_selector(".booking-classes-container", timeout=10000)
        except:
            print("Container not found. Is the date correct?")

        # Check if any classes exist at all
        classes = page.locator(".booking-class-item")
        count = await classes.count()
        print(f"Found {count} classes on this date.")

        if count == 0:
            print("No classes found. Exiting to avoid timeout.")
            return # This stops the script cleanly

        # If classes exist, wait for the specific one
        await page.wait_for_selector(".booking-class-item", timeout=5000)
        
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

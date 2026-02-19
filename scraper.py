import asyncio
import random
import os
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ==============================================================================
# CONFIGURATION
# ==============================================================================
URL = "https://www.kenousa.com/games/GVR/Green/draws.php"
CSV_FILE = "results.csv"
PAGES_TO_COLLECT = 50       # 50 pages x 10 games = 500 games per run
RANDOM_SLEEP_MAX = 120


# ==============================================================================
# HELPER: Load existing Game IDs from CSV
# ==============================================================================
def load_existing_ids():
    if not os.path.exists(CSV_FILE):
        return set()
    try:
        df = pd.read_csv(CSV_FILE, dtype={"Game ID": str})
        return set(df["Game ID"].str.strip())
    except Exception as e:
        print(f"[Warning] Could not read existing CSV: {e}")
        return set()


# ==============================================================================
# HELPER: Save new games to CSV
# ==============================================================================
def save_new_games(new_games: list, existing_ids: set):
    if not new_games:
        print("[Save] No new games to save.")
        return 0

    df_new = pd.DataFrame(new_games)
    df_new = df_new[~df_new["Game ID"].astype(str).isin(existing_ids)]
    df_new = df_new.drop_duplicates(subset=["Game ID"])

    if df_new.empty:
        print("[Save] All collected games already exist in the CSV.")
        return 0

    # Sort numerically, not alphabetically
    df_new["Game ID"] = df_new["Game ID"].astype(int)
    df_new = df_new.sort_values("Game ID", ascending=True)
    df_new["Game ID"] = df_new["Game ID"].astype(str)

    file_exists = os.path.exists(CSV_FILE)
    df_new.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[Save] Successfully added {len(df_new)} new games to {CSV_FILE}.")
    return len(df_new)


# ==============================================================================
# CORE: Extract all game rows currently visible on the page
# ==============================================================================
async def extract_visible_games(page) -> list:
    games = []
    try:
        game_nums = await page.locator("div.game-num").all()
        game_dates = await page.locator("div.game-date").all()
        game_draws = await page.locator("div.game-draw").all()

        print(f"[Extract] Found {len(game_nums)} game-num, {len(game_dates)} game-date, {len(game_draws)} game-draw divs.")

        count = min(len(game_nums), len(game_dates), len(game_draws))

        for i in range(count):
            game_id = (await game_nums[i].inner_text()).strip()
            timestamp = (await game_dates[i].inner_text()).strip()
            raw_numbers = (await game_draws[i].inner_text()).strip()
            numbers = "-".join(raw_numbers.split())

            if game_id.isdigit() and numbers:
                games.append({
                    "Game ID": game_id,
                    "Timestamp": timestamp,
                    "Numbers": numbers,
                    "Scraped At": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                })

    except Exception as e:
        print(f"[Extract] Error: {e}")

    return games


# ==============================================================================
# CORE: Click the back "10" button.
# Button order is always fixed:
#   index 0 = oldest
#   index 1 = 100-back
#   index 2 = 10-back  <-- this is what we want
#   index 3 = 10-forward
#   index 4 = 100-forward
#   index 5 = current
# ==============================================================================
async def click_back_10(page) -> bool:
    try:
        first_before = (await page.locator("div.game-num").first.inner_text()).strip()
        print(f"[Nav] First Game ID before click: {first_before}")

        # Always target the back "10" button by fixed position (index 2)
        back_button = page.locator("button.game-change").nth(2)

        count = await back_button.count()
        if count == 0:
            print("[Nav] Back button not found.")
            return False

        # Check it's not disabled (means we've reached the oldest available data)
        cls = await back_button.get_attribute("class") or ""
        if "disabled" in cls:
            print("[Nav] Back button is disabled. Reached oldest available data.")
            return False

        await back_button.click()
        print("[Nav] Clicked '10' back button. Waiting for data to update...")

        # Wait for the game divs to update
        for _ in range(15):
            await asyncio.sleep(1)
            try:
                first_after = (await page.locator("div.game-num").first.inner_text()).strip()
                if first_after != first_before:
                    print(f"[Nav] Data updated. First Game ID now: {first_after}")
                    return True
            except:
                pass

        print("[Nav] Data did not change after clicking.")
        return False

    except Exception as e:
        print(f"[Nav] Error: {e}")
        return False


# ==============================================================================
# MAIN SCRAPER
# ==============================================================================
async def run_scraper():
    sleep_seconds = random.randint(0, RANDOM_SLEEP_MAX)
    print(f"[Start] Sleeping {sleep_seconds}s for randomized staggering...")
    await asyncio.sleep(sleep_seconds)

    existing_ids = load_existing_ids()
    print(f"[Start] Loaded {len(existing_ids)} existing Game IDs from CSV.")

    all_collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        try:
            print(f"[Browser] Navigating to {URL}")
            await page.goto(URL, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(10)
            await page.screenshot(path="screenshot.png", full_page=True)
            print("[Debug] Screenshot saved.")

            try:
                await page.wait_for_selector("div.game-num", timeout=20000)
                print("[Setup] Game divs are ready.")
            except PlaywrightTimeout:
                print("[Error] Game divs never appeared. Check screenshot.png.")
                return

            seen_ids_this_run = set()

            for page_num in range(1, PAGES_TO_COLLECT + 1):
                print(f"\n[Loop] Scraping page {page_num} of {PAGES_TO_COLLECT}")

                page_games = await extract_visible_games(page)
                print(f"[Loop] Extracted {len(page_games)} games.")

                new_this_page = 0
                for game in page_games:
                    if game["Game ID"] not in seen_ids_this_run:
                        seen_ids_this_run.add(game["Game ID"])
                        all_collected.append(game)
                        new_this_page += 1

                print(f"[Loop] {new_this_page} new unique games. Running total: {len(all_collected)}")

                if page_num < PAGES_TO_COLLECT:
                    success = await click_back_10(page)
                    if not success:
                        print("[Loop] Could not go back further. Stopping early.")
                        break
                    await asyncio.sleep(2)

        except Exception as e:
            print(f"[Fatal] Unexpected error: {e}")
        finally:
            await browser.close()

    print(f"\n[Summary] Collected {len(all_collected)} total games this run.")
    saved = save_new_games(all_collected, existing_ids)
    print(f"[Summary] Run complete. {saved} new games written to disk.")


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    asyncio.run(run_scraper())

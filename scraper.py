import asyncio
import random
import time
import os
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ==============================================================================
# CONFIGURATION
# ==============================================================================
URL = "https://www.kenousa.com/games/GVR/Green/draws.php"
CSV_FILE = "results.csv"
TARGET_GAME_COUNT = 150   # How many historical games to collect per run
MAX_NAV_ATTEMPTS = 20     # Max times we'll try to click "back" to find more games
RANDOM_SLEEP_MAX = 120    # Max seconds to sleep at start (adds randomness to scheduling)


# ==============================================================================
# HELPER: Load existing Game IDs from CSV so we can skip duplicates
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
# HELPER: Save new games to CSV, appending to any existing data
# ==============================================================================
def save_new_games(new_games: list, existing_ids: set):
    if not new_games:
        print("[Save] No new games to save.")
        return 0

    df_new = pd.DataFrame(new_games)

    # Final deduplication pass against what was already on disk
    df_new = df_new[~df_new["Game ID"].astype(str).isin(existing_ids)]
    df_new = df_new.drop_duplicates(subset=["Game ID"])

    if df_new.empty:
        print("[Save] All collected games already exist in the CSV.")
        return 0

    # Sort oldest-first before appending so the file stays chronological
    df_new = df_new.sort_values("Game ID", ascending=True)

    file_exists = os.path.exists(CSV_FILE)
    df_new.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[Save] Successfully added {len(df_new)} new games to {CSV_FILE}.")
    return len(df_new)


# ==============================================================================
# CORE: Extract all game rows visible in the table right now
# ==============================================================================
async def extract_visible_games(page) -> list:
    games = []
    try:
        rows = await page.locator("#draws tbody tr").all()
        for row in rows:
            cells = await row.locator("td").all()
            if len(cells) < 3:
                continue

            game_id = (await cells[0].inner_text()).strip()
            timestamp = (await cells[1].inner_text()).strip()

            # Numbers may be rendered as individual "ball" spans or plain text
            balls = await cells[2].locator(".ball").all()
            if balls:
                ball_texts = [(await b.inner_text()).strip() for b in balls]
                numbers = "-".join(t for t in ball_texts if t)
            else:
                numbers = (await cells[2].inner_text()).strip().replace("\n", "-").replace(" ", "-")

            # Only store rows that look like real game data (numeric Game ID)
            if game_id.isdigit():
                games.append({
                    "Game ID": game_id,
                    "Timestamp": timestamp,
                    "Numbers": numbers,
                    "Scraped At": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                })
    except Exception as e:
        print(f"[Extract] Error reading table rows: {e}")

    return games


# ==============================================================================
# CORE: Try to click the "go back" navigation button on the XpertX widget
# Returns True if the click succeeded and the table refreshed, False otherwise
# ==============================================================================
async def click_back_button(page) -> bool:
    # The site uses a custom XpertX widget. We look for any icon that means "previous page"
    # Multiple selectors tried in order so we're resilient to minor site changes
    selectors = [
        "//span[contains(@class,'xpertx-icon-play-2')]/ancestor::a",
        "//span[contains(@class,'xpertx-icon-prev')]/ancestor::a",
        "//a[contains(@class,'prev')]",
        "#draws_previous",  # Standard DataTables "Previous" button
        ".paginate_button.previous",
    ]

    for selector in selectors:
        try:
            if selector.startswith("//"):
                locator = page.locator(f"xpath={selector}")
            else:
                locator = page.locator(selector)

            count = await locator.count()
            if count == 0:
                continue

            # Check if it's actually clickable (not disabled)
            is_disabled = await locator.first.get_attribute("class") or ""
            if "disabled" in is_disabled:
                print("[Nav] Back button found but is disabled (we've reached the end).")
                return False

            # Record the first Game ID currently visible so we can detect a page change
            first_row_before = await page.locator("#draws tbody tr:first-child td:first-child").inner_text()

            await locator.first.click()

            # Wait for the table to update (first row Game ID should change)
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    first_row_after = await page.locator("#draws tbody tr:first-child td:first-child").inner_text()
                    if first_row_after.strip() != first_row_before.strip():
                        print(f"[Nav] Page changed successfully. First game: {first_row_after.strip()}")
                        return True
                except:
                    pass

            print("[Nav] Clicked back but table did not change.")
            return False

        except Exception as e:
            print(f"[Nav] Selector '{selector}' failed: {e}")
            continue

    print("[Nav] Could not find any working back button.")
    return False


# ==============================================================================
# MAIN SCRAPER
# ==============================================================================
async def run_scraper():
    # --- Random stagger so 4 scheduled runs don't all hit the site at once ---
    sleep_seconds = random.randint(0, RANDOM_SLEEP_MAX)
    print(f"[Start] Sleeping {sleep_seconds}s for randomized staggering...")
    await asyncio.sleep(sleep_seconds)

    existing_ids = load_existing_ids()
    print(f"[Start] Loaded {len(existing_ids)} existing game IDs from CSV.")

    all_collected = []  # All games found this run (before dedup)

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

            # Give the JavaScript app time to fully render
            await asyncio.sleep(8)

            # --- Try to expand the view to show more games per page ---
            try:
                # Look for a "Show entries" dropdown (common in DataTables)
                dropdown = page.locator("select[name='draws_length'], select").first
                await dropdown.wait_for(state="attached", timeout=10000)
                for value in ["100", "50", "25"]:
                    try:
                        await page.select_option("select", value)
                        print(f"[Setup] Set table to show {value} entries.")
                        await asyncio.sleep(3)
                        break
                    except:
                        continue
            except Exception as e:
                print(f"[Setup] Could not change dropdown (will use default): {e}")

            # --- Wait for the table to be populated ---
            try:
                await page.wait_for_selector("#draws tbody tr", timeout=20000)
                print("[Setup] Table is ready.")
            except PlaywrightTimeout:
                print("[Error] Table never appeared. The site may have blocked us or changed.")
                return

            # ---------------------------------------------------------------
            # MAIN COLLECTION LOOP
            # We grab the current page, then navigate back, repeat until
            # we have TARGET_GAME_COUNT games or run out of history.
            # ---------------------------------------------------------------
            seen_ids_this_run = set()

            for attempt in range(1, MAX_NAV_ATTEMPTS + 1):
                print(f"\n[Loop] Navigation attempt {attempt}/{MAX_NAV_ATTEMPTS}")

                page_games = await extract_visible_games(page)
                print(f"[Loop] Extracted {len(page_games)} games from current view.")

                new_this_page = 0
                for game in page_games:
                    if game["Game ID"] not in seen_ids_this_run:
                        seen_ids_this_run.add(game["Game ID"])
                        all_collected.append(game)
                        new_this_page += 1

                print(f"[Loop] {new_this_page} unique games added this pass. Total so far: {len(all_collected)}")

                # Stop if we've collected enough
                if len(all_collected) >= TARGET_GAME_COUNT:
                    print(f"[Loop] Reached target of {TARGET_GAME_COUNT} games. Stopping navigation.")
                    break

                # Try to go back to older games
                success = await click_back_button(page)
                if not success:
                    print("[Loop] Could not navigate further back. Ending collection.")
                    break

        except Exception as e:
            print(f"[Fatal] Unexpected error: {e}")
        finally:
            await browser.close()

    # --- Save everything we found ---
    print(f"\n[Summary] Collected {len(all_collected)} total games this run.")
    saved = save_new_games(all_collected, existing_ids)
    print(f"[Summary] Run complete. {saved} new games written to disk.")


# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    asyncio.run(run_scraper())

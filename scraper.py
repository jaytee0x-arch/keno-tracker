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
PAGES_TO_COLLECT = 15       # 15 pages x 10 games = 150 games per run
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

    df_new = df_new.sort_values("Game ID", ascending=True)

    file_exists = os.path.exists(CSV_FILE)
    df_new.to_csv(CSV_FILE, mode="a", header=not file_exists, index=False)
    print(f"[Save] Successfully added {len(df_new)} new games to {CSV_FILE}.")
    return len(df_new)


# ==============================================================================
# CORE: Extract all game rows currently visible in the table
# ==============================================================================
async def extract_visible_games(page) -> list:
    games = []
    try:
        rows = await page.locator("table tr").all()
        print(f"[Extract] Found {len(rows)} table rows.")

        for row in rows:
            cells = await row.locator("td").all()

            if len(cells) < 4:
                continue

            game_id = (await cells[0].inner_text()).strip()
            timestamp = (await cells[1].inner_text()).strip()

            number_parts = []
            for cell in cells[2:]:
                text = (await cell.inner_text()).strip()
                if text.isdigit():
                    number_parts.append(text)

            numbers = "-".join(number_parts)

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
# CORE: Click the back arrow to go to the previous page of games
# ==============================================================================
async def click_back_one_page(page) -> bool:
    try:
        first_before = (await page.locator("table tr td:first-child").first.inner_text()).strip()
        print(f"[Nav] First Game ID before click: {first_before}")

        selectors = [
            "xpath=//a[img[contains(@src,'prev') or contains(@src,'back') or contains(@src,'left') or contains(@src,'arrow')]]",
            "xpath=//a[contains(@href,'prev') or contains(@title,'prev') or contains(@title,'Previous')]",
            "xpath=//img[contains(@src,'prev') or contains(@src,'back')]/parent::a",
        ]

        clicked = False
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    await locator.first.click()
                    clicked = True
                    print(f"[Nav] Clicked back using: {selector}")
                    break
            except:
                continue

        if not clicked:
            print("[Nav] Standard selectors failed. Printing all links for diagnosis...")
            links = await page.locator("a").all()
            for i, link in enumerate(links):
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).strip()
                print(f"  Link {i}: text='{text}' href='{href}'")
            return False

        for _ in range(15):
            await asyncio.sleep(1)
            try:
                first_after = (await page.locator("table tr td:first-child").first.inner_text()).strip()
                if first_after != first_before:
                    print(f"[Nav] Page changed. First Game ID now: {first_after}")
                    return True
            except:
                pass

        print("[Nav] Page did not change after clicking.")
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
            await asyncio.sleep(8)
            await page.screenshot(path="screenshot.png", full_page=True)
            print("[Debug] Screenshot saved.")

            # Wait for table to be ready
            try:
                await page.wait_for_selector("table tr", timeout=20000)
                print("[Setup] Table is ready.")
            except PlaywrightTimeout:
                print("[Error] Table never appeared. Check screenshot.png.")
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
                    success = await click_back_one_page(page)
                    if not success:
                        print("[Loop] Could not go back further. Stopping early.")
                        break
                    await asyncio.sleep(3)

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

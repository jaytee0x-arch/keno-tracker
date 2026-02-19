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
PAGES_TO_COLLECT = 15
RANDOM_SLEEP_MAX = 120


def load_existing_ids():
    if not os.path.exists(CSV_FILE):
        return set()
    try:
        df = pd.read_csv(CSV_FILE, dtype={"Game ID": str})
        return set(df["Game ID"].str.strip())
    except Exception as e:
        print(f"[Warning] Could not read existing CSV: {e}")
        return set()


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

            # ==================================================================
            # TEMPORARY DEBUG - identify all interactive elements on the page
            # ==================================================================
            print("\n[Debug] All buttons on page:")
            buttons = await page.locator("button, input[type='button'], input[type='submit']").all()
            for i, btn in enumerate(buttons):
                text = (await btn.inner_text()).strip()
                onclick = await btn.get_attribute("onclick") or ""
                cls = await btn.get_attribute("class") or ""
                print(f"  Button {i}: text='{text}' class='{cls}' onclick='{onclick}'")

            print("\n[Debug] All elements with onclick:")
            onclicks = await page.locator("[onclick]").all()
            for i, el in enumerate(onclicks):
                text = (await el.inner_text()).strip()[:50]
                onclick = await el.get_attribute("onclick") or ""
                tag = await el.evaluate("el => el.tagName")
                print(f"  Onclick {i}: tag='{tag}' text='{text}' onclick='{onclick}'")

            print("\n[Debug] All links including those with no text:")
            all_links = await page.locator("a").all()
            for i, link in enumerate(all_links):
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).strip()
                onclick = await link.get_attribute("onclick") or ""
                html = await link.evaluate("el => el.outerHTML")
                print(f"  Link {i}: text='{text}' href='{href}' onclick='{onclick}' html='{html[:150]}'")
            # ==================================================================

        except Exception as e:
            print(f"[Fatal] Unexpected error: {e}")
        finally:
            await browser.close()

    print(f"\n[Summary] Debug run complete. Check logs above to identify nav buttons.")


if __name__ == "__main__":
    asyncio.run(run_scraper())

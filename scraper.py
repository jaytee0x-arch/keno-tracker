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
TARGET_GAME_COUNT = 150
MAX_NAV_ATTEMPTS = 20
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
def save_new_games(new_games: list, existing_i

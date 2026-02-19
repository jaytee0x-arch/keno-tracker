import os
import smtplib
import pandas as pd
import numpy as np
from math import erf, sqrt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict

# ==============================================================================
# CONFIGURATION
# ==============================================================================
CSV_FILE = "results.csv"
GAMES_TO_ANALYZE = 500          # How many recent games to analyze
MIN_GAMES_REQUIRED = 100        # Don't analyze if we have fewer than this
ALERT_THRESHOLD = 90.0          # Only email if at least one number hits this %
MIN_NUMBERS_FOR_ALERT = 3       # Only alert if this many numbers exceed threshold

# Email config — loaded from GitHub Secrets (environment variables)
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# ==============================================================================
# KENO BOARD LAYOUT
# Numbers 1-80 arranged in an 8-row x 10-column grid:
#   Row 0:  1-10
#   Row 1: 11-20
#   ...
#   Row 7: 71-80
# ==============================================================================
BOARD_ROWS = 8
BOARD_COLS = 10

def get_position(number):
    """Returns (row, col) for a keno number 1-80."""
    n = number - 1
    return n // BOARD_COLS, n % BOARD_COLS

def get_neighbors(number):
    """Returns all valid neighbor numbers on the board (including diagonals)."""
    row, col = get_position(number)
    neighbors = []
    for dr in [-1, 0, 1]:
        for dc in [-1, 0, 1]:
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, col + dc
            if 0 <= r < BOARD_ROWS and 0 <= c < BOARD_COLS:
                neighbors.append(r * BOARD_COLS + c + 1)
    return neighbors

def get_board_region(number):
    """Returns a human-readable region name for the email summary."""
    row, col = get_position(number)
    vertical = "Top" if row < 3 else ("Middle" if row < 5 else "Bottom")
    horizontal = "Left" if col < 4 else ("Center" if col < 6 else "Right")
    return f"{vertical}-{horizontal}"

# ==============================================================================
# STATISTICS
# ==============================================================================
def norm_cdf(z):
    """Normal CDF using Python's built-in error function. No scipy needed."""
    return (1.0 + erf(z / sqrt(2.0))) / 2.0

def calculate_z_score(observed, n_games):
    """
    Calculate Z-score for a number's frequency.
    In keno, 20 of 80 numbers are drawn per game.
    P(any number appears) = 20/80 = 0.25
    Expected hits = n_games * 0.25
    Std dev = sqrt(n_games * 0.25 * 0.75)
    """
    p = 20 / 80  # = 0.25
    expected = n_games * p
    std_dev = sqrt(n_games * p * (1 - p))
    if std_dev == 0:
        return 0.0
    return (observed - expected) / std_dev

def z_to_confidence(z):
    """Converts Z-score to one-tailed confidence percentage."""
    return norm_cdf(z) * 100.0

# ==============================================================================
# CORE ANALYSIS
# ==============================================================================
def load_and_prepare_data():
    """Load CSV and return the most recent GAMES_TO_ANALYZE games."""
    if not os.path.exists(CSV_FILE):
        print("[Analyzer] No results.csv found. Skipping analysis.")
        return None

    df = pd.read_csv(CSV_FILE, dtype={"Game ID": str})
    df["Game ID"] = df["Game ID"].astype(int)
    df = df.sort_values("Game ID", ascending=True)

    if len(df) < MIN_GAMES_REQUIRED:
        print(f"[Analyzer] Only {len(df)} games available. Need at least {MIN_GAMES_REQUIRED}. Skipping.")
        return None

    # Use only the most recent GAMES_TO_ANALYZE games
    df = df.tail(GAMES_TO_ANALYZE).reset_index(drop=True)
    print(f"[Analyzer] Analyzing {len(df)} games (Game IDs {df['Game ID'].iloc[0]} to {df['Game ID'].iloc[-1]}).")
    return df

def count_frequencies(df):
    """Count how many times each number 1-80 appeared across all games."""
    freq = defaultdict(int)
    for _, row in df.iterrows():
        numbers_str = str(row["Numbers"])
        parts = numbers_str.replace(",", "-").split("-")
        for part in parts:
            part = part.strip()
            if part.isdigit():
                n = int(part)
                if 1 <= n <= 80:
                    freq[n] += 1
    return freq

def calculate_scores(freq, n_games):
    """
    For each number, calculate:
    1. Individual Z-score
    2. Cluster Z-score (average Z of self + neighbors)
    3. Spatial weighted score (blend of both)
    4. Confidence percentage
    """
    # Step 1: Z-score for every number
    z_scores = {}
    for n in range(1, 81):
        z_scores[n] = calculate_z_score(freq.get(n, 0), n_games)

    # Step 2: Cluster score = average Z of self + all neighbors
    cluster_scores = {}
    for n in range(1, 81):
        neighbors = get_neighbors(n)
        all_z = [z_scores[n]] + [z_scores[nb] for nb in neighbors]
        cluster_scores[n] = sum(all_z) / len(all_z)

    # Step 3: Spatial weighted score (60% cluster, 40% individual)
    # This rewards numbers that are hot AND surrounded by other hot numbers
    weighted_scores = {}
    for n in range(1, 81):
        weighted_scores[n] = (0.6 * cluster_scores[n]) + (0.4 * z_scores[n])

    # Step 4: Confidence based on individual Z-score
    confidence = {}
    for n in range(1, 81):
        confidence[n] = z_to_confidence(z_scores[n])

    return z_scores, cluster_scores, weighted_scores, confidence

def select_top_10(weighted_scores, confidence):
    """Select the top 10 numbers by spatial weighted score."""
    ranked = sorted(weighted_scores.keys(), key=lambda n: weighted_scores[n], reverse=True)
    return ranked[:10]

def find_dominant_cluster_region(top_10):
    """Identify the most common board region among the top 10 numbers."""
    region_counts = defaultdict(int)
    for n in top_10:
        region_counts[get_board_region(n)] += 1
    return max(region_counts, key=region_counts.get)

# ==============================================================================
# EMAIL
# ==============================================================================
def build_email(top_10, confidence, weighted_scores, z_scores, freq, n_games, dominant_region):
    """Build the HTML email content."""

    # Calculate the probability of this cluster appearing by chance
    # We use the product of individual p-values for the top numbers above threshold
    qualifying = [n for n in top_10 if confidence[n] >= ALERT_THRESHOLD]
    if qualifying:
        combined_p = 1.0
        for n in qualifying:
            p_value = 1.0 - (confidence[n] / 100.0)
            combined_p *= p_value
        combined_pct = combined_p * 100.0
        verdict = f"The probability of this cluster appearing by chance is less than {combined_pct:.2f}%."
    else:
        verdict = "Numbers are elevated but have not reached the strongest bias threshold."

    # Build the number rows
    rows_html = ""
    for rank, n in enumerate(top_10, 1):
        conf = confidence[n]
        hits = freq.get(n, 0)
        expected = round(n_games * 0.25, 1)
        region = get_board_region(n)

        if conf >= 99:
            badge = "🔴 STRONGEST BIAS"
            color = "#c0392b"
        elif conf >= 95:
            badge = "🟠 Strong Signal"
            color = "#e67e22"
        elif conf >= 90:
            badge = "🟡 Notable"
            color = "#f39c12"
        else:
            badge = "⚪ Elevated"
            color = "#7f8c8d"

        rows_html += f"""
        <tr>
            <td style="padding:8px;font-size:18px;font-weight:bold;color:{color};">{rank}.</td>
            <td style="padding:8px;font-size:18px;font-weight:bold;">Number {n}</td>
            <td style="padding:8px;font-size:16px;color:{color};">{conf:.1f}% Confidence</td>
            <td style="padding:8px;font-size:14px;color:#555;">{badge}</td>
            <td style="padding:8px;font-size:13px;color:#777;">{hits} hits vs {expected} expected | {region}</td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
    <div style="max-width:700px;margin:auto;background:white;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

      <div style="background:#1a1a2e;padding:24px;text-align:center;">
        <h1 style="color:#e94560;margin:0;">🎯 Keno Bias Alert</h1>
        <p style="color:#aaa;margin:8px 0 0;">10 Actionable Numbers Found</p>
      </div>

      <div style="padding:24px;">

        <div style="background:#f8f9fa;border-left:4px solid #e94560;padding:16px;border-radius:4px;margin-bottom:24px;">
          <h3 style="margin:0 0 8px;color:#1a1a2e;">Analysis Summary</h3>
          <p style="margin:0;color:#555;">
            A significant spatial cluster has been detected in the
            <strong>{dominant_region}</strong> region of the board
            over the last <strong>{n_games} games</strong>.
          </p>
        </div>

        <h3 style="color:#1a1a2e;">Top 10 "Play Today" Recommendations</h3>
        <table style="width:100%;border-collapse:collapse;">
          {rows_html}
        </table>

        <div style="background:#1a1a2e;color:#aaa;padding:16px;border-radius:4px;margin-top:24px;font-size:13px;">
          <strong style="color:white;">Statistical Verdict:</strong> {verdict}
        </div>

        <div style="background:#fff3cd;border:1px solid #ffc107;padding:12px;border-radius:4px;margin-top:16px;font-size:12px;color:#856404;">
          ⚠️ <strong>Reminder:</strong> This analysis identifies historical statistical anomalies only.
          Past frequency does not guarantee future results. Keno is a game of chance.
          Please play responsibly.
        </div>

      </div>
    </div>
    </body></html>
    """
    return html

def send_email(subject, html_body):
    """Send the alert email via Gmail SMTP."""
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("[Email] Missing email credentials. Set EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT secrets.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECIPIENT
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

        print(f"[Email] Alert sent successfully to {EMAIL_RECIPIENT}.")
        return True

    except Exception as e:
        print(f"[Email] Failed to send email: {e}")
        return False

# ==============================================================================
# MAIN
# ==============================================================================
def run_analyzer():
    print("\n" + "="*60)
    print("[Analyzer] Starting Keno Bias Analysis...")
    print("="*60)

    df = load_and_prepare_data()
    if df is None:
        return

    n_games = len(df)
    freq = count_frequencies(df)

    print(f"[Analyzer] Counted frequencies across {n_games} games.")

    z_scores, cluster_scores, weighted_scores, confidence = calculate_scores(freq, n_games)
    top_10 = select_top_10(weighted_scores, confidence)
    dominant_region = find_dominant_cluster_region(top_10)

    # Print full report to logs regardless of whether email is sent
    print(f"\n[Analyzer] Top 10 Numbers by Spatial Weighted Score:")
    print(f"{'Rank':<6}{'Number':<10}{'Hits':<8}{'Expected':<12}{'Z-Score':<12}{'Confidence':<14}{'Region'}")
    print("-" * 75)
    expected = round(n_games * 0.25, 1)
    for rank, n in enumerate(top_10, 1):
        hits = freq.get(n, 0)
        z = z_scores[n]
        conf = confidence[n]
        region = get_board_region(n)
        print(f"{rank:<6}{n:<10}{hits:<8}{expected:<12}{z:<12.3f}{conf:<14.1f}{region}")

    # Check if any number meets the alert threshold
    qualifying = [n for n in top_10 if confidence[n] >= ALERT_THRESHOLD]
    print(f"\n[Analyzer] Numbers above {ALERT_THRESHOLD}% confidence threshold: {len(qualifying)}")

    if len(qualifying) >= MIN_NUMBERS_FOR_ALERT:
        print(f"[Analyzer] Alert threshold met! Sending email...")
        html = build_email(top_10, confidence, weighted_scores, z_scores, freq, n_games, dominant_region)
        subject = f"🎯 Keno Bias Alert: {len(qualifying)} Strong Numbers Found in {dominant_region} Region"
        send_email(subject, html)
    else:
        print(f"[Analyzer] Game appears fair. No alert sent. (Need {MIN_NUMBERS_FOR_ALERT}+ numbers above {ALERT_THRESHOLD}%)")

    print("\n[Analyzer] Analysis complete.")

if __name__ == "__main__":
    run_analyzer()

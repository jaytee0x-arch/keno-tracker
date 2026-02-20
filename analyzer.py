import os
import smtplib
import base64
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server use
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
from math import erf, sqrt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from collections import defaultdict
from io import BytesIO

# ==============================================================================
# CONFIGURATION
# ==============================================================================
CSV_FILE = "results.csv"
HEATMAP_FILE = "heatmap.png"
GAMES_TO_ANALYZE = 500
MIN_GAMES_REQUIRED = 100
ALERT_THRESHOLD = 90.0
MIN_NUMBERS_FOR_ALERT = 3

EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# ==============================================================================
# KENO BOARD LAYOUT
# ==============================================================================
BOARD_ROWS = 8
BOARD_COLS = 10

def get_position(number):
    n = number - 1
    return n // BOARD_COLS, n % BOARD_COLS

def get_neighbors(number):
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
    row, col = get_position(number)
    vertical = "Top" if row < 3 else ("Middle" if row < 5 else "Bottom")
    horizontal = "Left" if col < 4 else ("Center" if col < 6 else "Right")
    return f"{vertical}-{horizontal}"

# ==============================================================================
# STATISTICS
# ==============================================================================
def norm_cdf(z):
    return (1.0 + erf(z / sqrt(2.0))) / 2.0

def calculate_z_score(observed, n_games):
    p = 20 / 80
    expected = n_games * p
    std_dev = sqrt(n_games * p * (1 - p))
    if std_dev == 0:
        return 0.0
    return (observed - expected) / std_dev

def z_to_confidence(z):
    return norm_cdf(z) * 100.0

# ==============================================================================
# CORE ANALYSIS
# ==============================================================================
def load_and_prepare_data():
    if not os.path.exists(CSV_FILE):
        print("[Analyzer] No results.csv found. Skipping analysis.")
        return None
    df = pd.read_csv(CSV_FILE, dtype={"Game ID": str})
    df["Game ID"] = df["Game ID"].astype(int)
    df = df.sort_values("Game ID", ascending=True)
    if len(df) < MIN_GAMES_REQUIRED:
        print(f"[Analyzer] Only {len(df)} games available. Need at least {MIN_GAMES_REQUIRED}. Skipping.")
        return None
    df = df.tail(GAMES_TO_ANALYZE).reset_index(drop=True)
    print(f"[Analyzer] Analyzing {len(df)} games (Game IDs {df['Game ID'].iloc[0]} to {df['Game ID'].iloc[-1]}).")
    return df

def count_frequencies(df):
    freq = defaultdict(int)
    for _, row in df.iterrows():
        parts = str(row["Numbers"]).replace(",", "-").split("-")
        for part in parts:
            part = part.strip()
            if part.isdigit():
                n = int(part)
                if 1 <= n <= 80:
                    freq[n] += 1
    return freq

def calculate_scores(freq, n_games):
    z_scores = {n: calculate_z_score(freq.get(n, 0), n_games) for n in range(1, 81)}

    cluster_scores = {}
    for n in range(1, 81):
        neighbors = get_neighbors(n)
        all_z = [z_scores[n]] + [z_scores[nb] for nb in neighbors]
        cluster_scores[n] = sum(all_z) / len(all_z)

    weighted_scores = {n: (0.6 * cluster_scores[n]) + (0.4 * z_scores[n]) for n in range(1, 81)}
    confidence = {n: z_to_confidence(z_scores[n]) for n in range(1, 81)}

    return z_scores, cluster_scores, weighted_scores, confidence

def select_top_10(weighted_scores):
    ranked = sorted(weighted_scores.keys(), key=lambda n: weighted_scores[n], reverse=True)
    return ranked[:10]

def find_dominant_cluster_region(top_10):
    region_counts = defaultdict(int)
    for n in top_10:
        region_counts[get_board_region(n)] += 1
    return max(region_counts, key=region_counts.get)

# ==============================================================================
# HEATMAP GENERATION
# ==============================================================================
def generate_heatmap(weighted_scores, confidence, top_10, n_games):
    """
    Generate an 8x10 keno board heatmap colored by spatial weighted score.
    Top 10 numbers are circled and labeled with their confidence percentage.
    Returns image bytes and also saves to disk.
    """

    # Build the 8x10 grid of weighted scores
    grid = np.zeros((BOARD_ROWS, BOARD_COLS))
    for n in range(1, 81):
        row, col = get_position(n)
        grid[row, col] = weighted_scores[n]

    # Custom colormap: deep blue (cold) → black (neutral) → deep red (hot)
    colors = [
        (0.05, 0.15, 0.45),   # Cold: deep navy
        (0.10, 0.30, 0.60),   # Cool
        (0.15, 0.15, 0.15),   # Neutral: near black
        (0.55, 0.10, 0.10),   # Warm
        (0.85, 0.05, 0.05),   # Hot: deep red
        (1.00, 0.85, 0.00),   # Hottest: gold
    ]
    cmap = LinearSegmentedColormap.from_list("keno_heat", colors, N=256)

    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor("#0d0d0d")
    ax.set_facecolor("#0d0d0d")

    # Draw the heatmap
    im = ax.imshow(grid, cmap=cmap, aspect="auto", interpolation="gaussian")

    # Draw each number cell
    for n in range(1, 81):
        row, col = get_position(n)
        is_top10 = n in top_10
        conf = confidence[n]

        # Number label — white for top 10, gray for others
        label_color = "white" if is_top10 else "#888888"
        fontsize = 11 if is_top10 else 9
        fontweight = "bold" if is_top10 else "normal"

        ax.text(col, row, str(n),
                ha="center", va="center" if not is_top10 else "top",
                color=label_color,
                fontsize=fontsize,
                fontweight=fontweight)

        # For top 10: add confidence % below the number and draw a circle
        if is_top10:
            ax.text(col, row + 0.22, f"{conf:.0f}%",
                    ha="center", va="center",
                    color="#FFD700",
                    fontsize=7.5,
                    fontweight="bold")

            # Gold circle outline
            circle = mpatches.Circle(
                (col, row), 0.44,
                linewidth=2.2,
                edgecolor="#FFD700",
                facecolor="none",
                zorder=5
            )
            ax.add_patch(circle)

    # Grid lines
    for x in np.arange(-0.5, BOARD_COLS, 1):
        ax.axvline(x, color="#333333", linewidth=0.5)
    for y in np.arange(-0.5, BOARD_ROWS, 1):
        ax.axhline(y, color="#333333", linewidth=0.5)

    # Axis labels
    ax.set_xticks(range(BOARD_COLS))
    ax.set_xticklabels([str(i + 1) for i in range(BOARD_COLS)], color="#666", fontsize=8)
    ax.set_yticks(range(BOARD_ROWS))
    ax.set_yticklabels([f"{i * 10 + 1}-{i * 10 + 10}" for i in range(BOARD_ROWS)], color="#666", fontsize=8)
    ax.tick_params(length=0)

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white", fontsize=8)
    cbar.set_label("Spatial Weighted Score", color="white", fontsize=9)

    # Title and subtitle
    top_10_str = "  |  ".join([f"{n} ({confidence[n]:.0f}%)" for n in top_10])
    fig.suptitle("Keno Bias Heatmap — Spatial Weighted Score",
                 color="white", fontsize=15, fontweight="bold", y=0.98)
    ax.set_title(f"Top 10: {top_10_str}\nBased on last {n_games} games",
                 color="#aaaaaa", fontsize=8, pad=10)

    # Legend
    legend_elements = [
        mpatches.Patch(facecolor="#8B0000", label="Hot Zone (above average)"),
        mpatches.Patch(facecolor="#00008B", label="Cold Zone (below average)"),
        mpatches.Circle((0, 0), radius=0.1, edgecolor="#FFD700",
                        facecolor="none", label="Top 10 Picks"),
    ]
    ax.legend(handles=legend_elements, loc="lower center",
              bbox_to_anchor=(0.5, -0.08), ncol=3,
              facecolor="#1a1a1a", edgecolor="#444",
              labelcolor="white", fontsize=8)

    plt.tight_layout(rect=[0, 0.03, 1, 0.96])

    # Save to disk (for GitHub repository)
    plt.savefig(HEATMAP_FILE, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"[Heatmap] Saved to {HEATMAP_FILE}")

    # Also return as bytes (for email embedding)
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    img_bytes = buf.read()
    plt.close(fig)

    print("[Heatmap] Generated successfully.")
    return img_bytes

# ==============================================================================
# EMAIL
# ==============================================================================
def build_email_html(top_10, confidence, freq, n_games, dominant_region):
    qualifying = [n for n in top_10 if confidence[n] >= ALERT_THRESHOLD]
    if qualifying:
        combined_p = 1.0
        for n in qualifying:
            combined_p *= (1.0 - confidence[n] / 100.0)
        verdict = f"The probability of this cluster appearing by chance is less than {combined_p * 100:.2f}%."
    else:
        verdict = "Numbers are elevated but have not reached the strongest bias threshold."

    rows_html = ""
    expected = round(n_games * 0.25, 1)
    for rank, n in enumerate(top_10, 1):
        conf = confidence[n]
        hits = freq.get(n, 0)
        region = get_board_region(n)

        if conf >= 99:
            badge, color = "🔴 STRONGEST BIAS", "#c0392b"
        elif conf >= 95:
            badge, color = "🟠 Strong Signal", "#e67e22"
        elif conf >= 90:
            badge, color = "🟡 Notable", "#f39c12"
        else:
            badge, color = "⚪ Elevated", "#7f8c8d"

        rows_html += f"""
        <tr>
            <td style="padding:8px;font-size:18px;font-weight:bold;color:{color};">{rank}.</td>
            <td style="padding:8px;font-size:18px;font-weight:bold;">Number {n}</td>
            <td style="padding:8px;font-size:16px;color:{color};">{conf:.1f}% Confidence</td>
            <td style="padding:8px;font-size:14px;color:#555;">{badge}</td>
            <td style="padding:8px;font-size:13px;color:#777;">{hits} hits vs {expected} expected | {region}</td>
        </tr>"""

    return f"""
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
        <h3 style="color:#1a1a2e;">🗺️ Board Heatmap</h3>
        <p style="color:#555;font-size:13px;">Gold circles = Top 10 picks. Red zones = above average frequency. Blue zones = below average.</p>
        <img src="cid:heatmap" style="width:100%;border-radius:6px;border:1px solid #eee;" alt="Keno Heatmap"/>
        <h3 style="color:#1a1a2e;margin-top:24px;">Top 10 "Play Today" Recommendations</h3>
        <table style="width:100%;border-collapse:collapse;">{rows_html}</table>
        <div style="background:#1a1a2e;color:#aaa;padding:16px;border-radius:4px;margin-top:24px;font-size:13px;">
          <strong style="color:white;">Statistical Verdict:</strong> {verdict}
        </div>
        <div style="background:#fff3cd;border:1px solid #ffc107;padding:12px;border-radius:4px;margin-top:16px;font-size:12px;color:#856404;">
          ⚠️ <strong>Reminder:</strong> This analysis identifies historical statistical anomalies only.
          Past frequency does not guarantee future results. Keno is a game of chance. Please play responsibly.
        </div>
      </div>
    </div>
    </body></html>"""

def send_email(subject, html_body, img_bytes):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT]):
        print("[Email] Missing credentials. Check GitHub Secrets.")
        return False
    try:
        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECIPIENT

        msg_alt = MIMEMultipart("alternative")
        msg.attach(msg_alt)
        msg_alt.attach(MIMEText(html_body, "html"))

        # Embed heatmap image inline
        img = MIMEImage(img_bytes)
        img.add_header("Content-ID", "<heatmap>")
        img.add_header("Content-Disposition", "inline", filename="heatmap.png")
        msg.attach(img)

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
    print("\n" + "=" * 60)
    print("[Analyzer] Starting Keno Bias Analysis...")
    print("=" * 60)

    df = load_and_prepare_data()
    if df is None:
        return

    n_games = len(df)
    freq = count_frequencies(df)

    z_scores, cluster_scores, weighted_scores, confidence = calculate_scores(freq, n_games)
    top_10 = select_top_10(weighted_scores)
    dominant_region = find_dominant_cluster_region(top_10)

    print(f"\n[Analyzer] Top 10 Numbers by Spatial Weighted Score:")
    print(f"{'Rank':<6}{'Number':<10}{'Hits':<8}{'Expected':<12}{'Z-Score':<12}{'Confidence':<14}{'Region'}")
    print("-" * 75)
    expected = round(n_games * 0.25, 1)
    for rank, n in enumerate(top_10, 1):
        print(f"{rank:<6}{n:<10}{freq.get(n,0):<8}{expected:<12}{z_scores[n]:<12.3f}{confidence[n]:<14.1f}{get_board_region(n)}")

    # Always generate the heatmap (saves to repo regardless of alert)
    img_bytes = generate_heatmap(weighted_scores, confidence, top_10, n_games)

    qualifying = [n for n in top_10 if confidence[n] >= ALERT_THRESHOLD]
    print(f"\n[Analyzer] Numbers above {ALERT_THRESHOLD}% confidence: {len(qualifying)}")

    if len(qualifying) >= MIN_NUMBERS_FOR_ALERT:
        print("[Analyzer] Alert threshold met! Sending email...")
        html = build_email_html(top_10, confidence, freq, n_games, dominant_region)
        subject = f"🎯 Keno Bias Alert: {len(qualifying)} Strong Numbers Found in {dominant_region} Region"
        send_email(subject, html, img_bytes)
    else:
        print(f"[Analyzer] Game appears fair. No email sent. (Need {MIN_NUMBERS_FOR_ALERT}+ numbers above {ALERT_THRESHOLD}%)")

    print("\n[Analyzer] Analysis complete.")

if __name__ == "__main__":
    run_analyzer()

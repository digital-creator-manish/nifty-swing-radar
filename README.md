# Nifty Swing Radar

A Python CLI scanner that screens liquid **NSE Nifty 100** stocks for **2–7 day swing trading setups**, calculates exact ATR-based position sizing capped by your `MAX_RISK_INR`, logs paper-trading signals to `trades_log.csv`, and sends formatted alert cards to Telegram (no broker APIs required).

> **Disclaimer:** Educational scanner only. Past or technical breakouts do not guarantee profits. Always review charts and manage risk independently.

---

## Strategy & Risk Rules

A stock qualifies **only** if all three conditions hold on the latest daily candle (`4mo` lookback):
1. **Uptrend Alignment:** `Close > SMA_20 > SMA_50`
2. **20-Day Breakout:** `Close > highest High of the previous 20 trading days`
3. **Volume Confirmation:** `Volume >= 1.3 * 20-day average Volume`

Position sizing is strictly capped by `MAX_RISK_INR`:
- `risk_per_share = round(1.5 * ATR_14, 2)`
- `qty = int(MAX_RISK_INR // risk_per_share)` (skipped if `qty < 1`)
- `entry = round(Close, 2)`
- `stop_loss = round(entry - risk_per_share, 2)`
- `target = round(entry + 2 * risk_per_share, 2)` (`1:2` Risk-to-Reward)

Qualifying stocks are ranked by volume ratio (`Volume / SMA_20_Volume`) descending, keeping only the **top 3 setups** by default (configurable via `TOP_N` in `.env` or `--top`, with any extra qualifying stocks listed in a footer line).

---

## Setup

### 1. Create Virtual Environment & Install Dependencies
```bash
cd /home/manish/.gemini/antigravity/scratch/nifty-swing-radar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
On Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 2. Get Telegram Credentials via `@BotFather`
1. Open Telegram and search for **`@BotFather`** (verified blue checkmark).
2. Send `/newbot`, choose a display name (e.g., `Nifty Swing Radar`) and username (must end in `bot`, e.g., `my_nifty_radar_bot`).
3. Copy the **HTTP API token** `@BotFather` gives you (`123456789:ABC...`).
4. Start a chat with your new bot by opening its link and clicking **Start** (or send `/start`).
5. Get your **Chat ID**:
   - Visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in your browser (or message `@userinfobot`), and copy the numeric `"id"` under `"chat"`.
6. Create your `.env` file from `.env.example`:
   ```bash
   cp .env.example .env
   ```
   Fill in `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `MAX_RISK_INR` (default `250`).

### 3. Run Manually
Test safely without sending Telegram messages or writing `trades_log.csv`:
```bash
.venv/bin/python scanner.py --dry-run
```
Run live (sends Telegram alert + appends to `trades_log.csv`):
```bash
.venv/bin/python scanner.py
```

---

## Schedule Automatically (Weekdays at 3:10 PM IST)

Running at **3:10 PM IST** (20 minutes before NSE's 3:30 PM close) lets you review daily breakout confirmation and place GTT orders before market close.

### Linux / macOS (`cron`)
Assuming your machine timezone is IST (`Asia/Kolkata`):
1. Open crontab:
   ```bash
   crontab -e
   ```
2. Add this line:
   ```cron
   10 15 * * 1-5 cd /home/manish/.gemini/antigravity/scratch/nifty-swing-radar && /home/manish/.gemini/antigravity/scratch/nifty-swing-radar/.venv/bin/python scanner.py >> /home/manish/.gemini/antigravity/scratch/nifty-swing-radar/cron.log 2>&1
   ```
*(If your server runs in UTC instead of IST, 3:10 PM IST = `09:40 UTC`, so use `40 9 * * 1-5`.)*

### Windows (Task Scheduler via `schtasks`)
Run this in an elevated or user Command Prompt (adjust paths to your project location):
```bat
schtasks /Create /TN "NiftySwingRadar" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:10 /TR "cmd /c cd /d C:\path\to\nifty-swing-radar && .venv\Scripts\python.exe scanner.py" /F
```

### GitHub Actions (Free Cloud Schedule — No Billing Required)
A ready-to-use workflow is included at `.github/workflows/swing-radar.yml` (runs hourly during NSE weekday hours `9:35 AM – 3:10 PM IST`, plus manual **Run workflow**).

1. Create a **Private** repository on GitHub and upload/push all files (`.gitignore` automatically skips `.env` and `.venv/`).
2. Open **Settings → Secrets and variables → Actions → Secrets** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. *(Optional)* Under **Variables**, add `MAX_RISK_INR` (default `250`) and `TOP_N` (default `3`).
4. Open the **Actions** tab and click **Run workflow** to test immediately.

### Two-Way Telegram Remote Control (Optional — Cloudflare Worker)
To trigger scans directly from Telegram using `/scan`, `/scan 500`, or `/status`, deploy `cloudflare-worker/worker.js` to Cloudflare Workers (free tier):

1. Create a **Fine-Grained GitHub Personal Access Token** (`github.com/settings/tokens?type=beta`) scoped **only** to `nifty-swing-radar` with:
   - **Actions:** Read and write
   - **Contents:** Read-only (for `/status`)
2. Deploy `cloudflare-worker/worker.js` in the Cloudflare Dashboard (Workers & Pages → Create Worker, paste `worker.js`, and deploy) or with Wrangler:
   ```bash
   cd cloudflare-worker
   npx wrangler deploy
   ```
3. Add these **Variables & Secrets** to your Worker:
   - `GITHUB_OWNER` = `digital-creator-manish`
   - `GITHUB_REPO` = `nifty-swing-radar`
   - `TELEGRAM_BOT_TOKEN` = your BotFather token (Secret)
   - `ALLOWED_CHAT_ID` = your numeric Telegram Chat ID (Secret)
   - `WEBHOOK_SECRET` = any random password string you invent, e.g. `my_secret_9821` (Secret)
   - `GITHUB_TOKEN` = your fine-grained GitHub PAT (Secret)
4. Register the webhook with Telegram once (open in browser or run with `curl`):
   ```bash
   curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://<your-worker>.workers.dev", "secret_token": "<WEBHOOK_SECRET>"}'
   ```
5. Send `/help` or `/scan` to your bot in Telegram!

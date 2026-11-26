# Nifty Swing Radar — Agent Context & Architecture

> **Important Security Rule:** Never commit `.env`, `.venv/`, bot tokens, or API secrets to git. All credentials live in `.env` (local), GitHub Secrets, and Cloudflare Worker Secrets.

## Overview
`nifty-swing-radar` scans liquid NSE Nifty 100 stocks for 2–7 day swing breakout setups using daily Yahoo Finance data, calculates ATR-based position sizing capped by `MAX_RISK_INR`, logs triggered setups to `trades_log.csv`, and delivers Markdown trade alert cards to Telegram.

It also supports two-way remote control from Telegram (`/scan`, `/scan 500`, `/status`, `/dryrun`, `/help`) via a Cloudflare Worker relay that dispatches the GitHub Actions workflow.

---

## Architecture

```text
Telegram User (/scan)
   │
   ▼
Cloudflare Worker (nifty-swing-radar-relay.coolms123.workers.dev)
   │  verifies X-Telegram-Bot-Api-Secret-Token + ALLOWED_CHAT_ID
   ▼
GitHub Actions (.github/workflows/swing-radar.yml)
   │  also runs on weekday cron: 9:35–14:35 IST hourly + 15:10 IST
   ▼
scanner.py ──▶ Yahoo Finance (.NS) + ATR sizing ──▶ Telegram sendMessage
                                                └──▶ trades_log.csv (committed to main)
```

---

## Key Files

- `scanner.py` — Core CLI scanner, 3-filter rule engine, ATR position sizing, Telegram `sendMessage` sender (with plain-text fallback and sanitized error handling), `--dry-run`, and `--top` flags.
- `watchlist.py` — `NIFTY_100_TICKERS` list (`.NS` suffix for Yahoo Finance; `TATAMOTORS.NS` was replaced by post-demerger `TMCV.NS` & `TMPV.NS`, and `LTIM.NS` was removed due to 404).
- `logger.py` — Appends triggered setups to `trades_log.csv` (`date,symbol,entry,stop_loss,target,qty,capital_required,vol_ratio,status`).
- `.github/workflows/swing-radar.yml` — Scheduled (`5 4-9 * * 1-5` and `40 9 * * 1-5` UTC) and `workflow_dispatch` runner (`dry_run`, `max_risk_inr`, `top_n` inputs). Commits `trades_log.csv` changes back to `main`.
- `cloudflare-worker/worker.js` — Webhook handler for `/start`, `/help`, `/scan [risk]`, `/dryrun [risk]`, and `/status`.
- `cloudflare-worker/wrangler.toml` — Worker config (`nifty-swing-radar-relay`).
- `.env.example` — Template for `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MAX_RISK_INR`, `TOP_N`.
- `requirements.txt` — `yfinance`, `pandas`, `requests`, `python-dotenv`.

---

## Strategy & Risk Rules (Do Not Change Silently)
A stock qualifies on the latest daily candle (`4mo` lookback) only when:
1. `Close > SMA_20 > SMA_50`
2. `Close > highest High of the previous 20 trading days`
3. `Volume >= 1.3 * SMA_20_Volume`

Position sizing:
- `risk_per_share = round(1.5 * ATR_14, 2)`
- `qty = int(MAX_RISK_INR // risk_per_share)` (skip if `qty < 1`)
- `entry = round(Close, 2)`
- `stop_loss = round(entry - risk_per_share, 2)`
- `target = round(entry + 2 * risk_per_share, 2)` (`1:2` Risk-to-Reward)
- Sort qualifying setups by `vol_ratio` descending; send top `TOP_N` (default `3`) plus a footer line listing any runner-ups.

---

## Local Verification Commands

```bash
# Dry-run without Telegram or writing trades_log.csv
.venv/bin/python scanner.py --dry-run

# Override setup count once
.venv/bin/python scanner.py --dry-run --top 5

# Live run (requires .env)
.venv/bin/python scanner.py
```

---

## Deployment & Secret Inventory (Names Only)

- **GitHub Repo:** `digital-creator-manish/nifty-swing-radar` (`main` branch)
  - **GitHub Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - **GitHub Variables:** `MAX_RISK_INR` (`250`), `TOP_N` (`3`)
- **Cloudflare Worker:** `nifty-swing-radar-relay` (`https://nifty-swing-radar-relay.coolms123.workers.dev/`)
  - **Plaintext Vars:** `GITHUB_OWNER`, `GITHUB_REPO`
  - **Secrets:** `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_ID`, `WEBHOOK_SECRET`, `GITHUB_TOKEN` (fine-grained PAT: `Actions: Read & write`, `Contents: Read-only` on this repo)
- **Note on local environment:** `git` may not be installed locally; updates can be pushed cleanly via the `gh` CLI (`gh api` Git Data API) without overwriting remote `trades_log.csv` commits.

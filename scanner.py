"""Nifty 100 2-7 day swing trading scanner with ATR risk sizing and Telegram alerts."""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

from logger import append_signals
from watchlist import NIFTY_100_TICKERS

IST = timezone(timedelta(hours=5, minutes=30))
BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class SwingSetup:
    """Qualified swing trading setup with exact ATR-based risk sizing."""

    date: str
    symbol: str
    entry: float
    stop_loss: float
    target: float
    qty: int
    capital_required: float
    risk_per_share: float
    max_loss_inr: float
    vol_ratio: float
    sma_20: float
    sma_50: float
    atr_14: float

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "symbol": self.symbol,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "qty": self.qty,
            "capital_required": self.capital_required,
            "vol_ratio": self.vol_ratio,
            "status": "OPEN",
        }


def _compute_atr_14(df: pd.DataFrame) -> float:
    """Compute 14-day Average True Range from daily OHLC data."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_series = tr.rolling(14).mean()
    val = atr_series.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def evaluate_ticker(
    ticker: str,
    max_risk_inr: float,
    period: str = "4mo",
) -> SwingSetup | None:
    """Download 4mo daily data for one ticker and evaluate swing breakout rules."""
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if len(df) < 51:
        return None

    sma_20 = df["Close"].rolling(20).mean()
    sma_50 = df["Close"].rolling(50).mean()
    sma_20_vol = df["Volume"].rolling(20).mean()

    latest = df.iloc[-1]
    close_val = float(latest["Close"])
    vol_val = float(latest["Volume"])
    sma_20_val = float(sma_20.iloc[-1])
    sma_50_val = float(sma_50.iloc[-1])
    sma_20_vol_val = float(sma_20_vol.iloc[-1])

    if any(pd.isna(x) for x in (close_val, sma_20_val, sma_50_val, sma_20_vol_val)) or sma_20_vol_val <= 0:
        return None

    # 1. Uptrend Alignment: Close > SMA_20 > SMA_50
    if not (close_val > sma_20_val > sma_50_val):
        return None

    # 2. 20-Day Breakout: Close > highest High of the previous 20 trading days
    prev_20_high = float(df["High"].iloc[-21:-1].max())
    if close_val <= prev_20_high:
        return None

    # 3. Volume Confirmation: Volume >= 1.3 * 20-day average Volume
    vol_ratio = vol_val / sma_20_vol_val
    if vol_ratio < 1.3:
        return None

    # ATR-based position sizing strictly capped by MAX_RISK_INR
    atr_14 = _compute_atr_14(df)
    risk_per_share = round(1.5 * atr_14, 2)
    if risk_per_share <= 0:
        return None

    qty = int(max_risk_inr // risk_per_share)
    if qty < 1:
        return None

    entry = round(close_val, 2)
    stop_loss = round(entry - risk_per_share, 2)
    if stop_loss <= 0:
        return None
    target = round(entry + (2 * risk_per_share), 2)
    capital_required = round(qty * entry, 2)
    max_loss_actual = round(qty * risk_per_share, 2)

    bar_idx = df.index[-1]
    bar_date = bar_idx.strftime("%Y-%m-%d") if hasattr(bar_idx, "strftime") else str(bar_idx)[:10]

    return SwingSetup(
        date=bar_date,
        symbol=ticker.removesuffix(".NS"),
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        qty=qty,
        capital_required=capital_required,
        risk_per_share=risk_per_share,
        max_loss_inr=max_loss_actual,
        vol_ratio=round(vol_ratio, 2),
        sma_20=round(sma_20_val, 2),
        sma_50=round(sma_50_val, 2),
        atr_14=round(atr_14, 2),
    )


def scan_market(
    tickers: list[str],
    max_risk_inr: float,
    max_workers: int = 8,
    top_n: int | None = None,
) -> tuple[list[SwingSetup], list[str]]:
    """Scan all tickers concurrently with per-ticker error isolation."""
    qualifying: list[SwingSetup] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(tickers)))) as pool:
        future_map = {
            pool.submit(evaluate_ticker, ticker, max_risk_inr): ticker
            for ticker in tickers
        }
        for future in as_completed(future_map):
            ticker = future_map[future]
            try:
                setup = future.result()
                if setup is not None:
                    qualifying.append(setup)
            except Exception as exc:
                errors.append(f"{ticker}: {exc}")

    qualifying.sort(key=lambda s: s.vol_ratio, reverse=True)
    if top_n is not None and top_n > 0:
        return qualifying[:top_n], errors
    return qualifying, errors


def format_telegram_message(
    setups: list[SwingSetup],
    scan_date: str,
    max_risk_inr: float,
    scanned_count: int,
    error_count: int,
    runner_ups: list[SwingSetup] | None = None,
) -> str:
    """Format a single Markdown alert message for Telegram."""
    if not setups:
        return (
            f"*Nifty Swing Radar — {scan_date}*\n"
            f"Scanned `{scanned_count}` Nifty 100 stocks (Errors: {error_count}).\n\n"
            f"No setups met all 3 filters (Uptrend + 20D Breakout + 1.3x Volume) today.\n"
            f"Capital preserved — zero trades triggered."
        )

    runner_ups = runner_ups or []
    total_qualified = len(setups) + len(runner_ups)
    lines = [
        f"*Nifty Swing Radar — {scan_date}*",
        f"Max Risk Cap: `Rs. {max_risk_inr:,.0f}` | Showing Top `{len(setups)}` of `{total_qualified}` Qualified",
        "",
    ]
    for idx, s in enumerate(setups, start=1):
        lines.extend(
            [
                f"*{idx}. {s.symbol}* (Vol `{s.vol_ratio:.2f}x`)",
                f"• Buy Price: `Rs. {s.entry:,.2f}`",
                f"• Quantity: `{s.qty}` shares",
                f"• Total Capital Needed: `Rs. {s.capital_required:,.2f}`",
                f"• GTT Stop-Loss: `Rs. {s.stop_loss:,.2f}` (Max Loss: `Rs. {s.max_loss_inr:,.2f}`)",
                f"• GTT Target (1:2): `Rs. {s.target:,.2f}`",
                "",
            ]
        )
    if runner_ups:
        others = ", ".join(f"{r.symbol} (`{r.vol_ratio:.2f}x`)" for r in runner_ups)
        lines.append(f"Also qualified today: {others}")
        lines.append("")
    lines.append("_Educational scanner only. Verify levels before placing GTT orders._")
    return "\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    """Deliver a Markdown message via Telegram Bot API without leaking token in errors."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload_body = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload_body, timeout=15)

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.ok and data.get("ok"):
        return

    desc = str(data.get("description") or f"HTTP {resp.status_code}")

    # Retry once as plain text if Markdown entity parsing fails
    if resp.status_code == 400 and "parse entities" in desc.lower():
        plain_body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        retry = requests.post(url, json=plain_body, timeout=15)
        try:
            retry_data = retry.json()
        except ValueError:
            retry_data = {}
        if retry.ok and retry_data.get("ok"):
            return
        desc = str(retry_data.get("description") or f"HTTP {retry.status_code}")

    if "chat not found" in desc.lower():
        raise RuntimeError(
            "Telegram error (400): chat not found. Open your bot in Telegram, click Start "
            "(send /start), and verify TELEGRAM_CHAT_ID in .env is your numeric chat ID."
        )
    raise RuntimeError(f"Telegram API error ({resp.status_code}): {desc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nifty-swing-radar",
        description="Scan NSE Nifty 100 stocks for 2-7 day swing setups and alert via Telegram.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the formatted alert to terminal without sending to Telegram or writing trades_log.csv.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Max setups to send (defaults to TOP_N in .env or 3; pass 0 to send all qualifying).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(BASE_DIR / ".env")
    args = parse_args(argv)

    max_risk_inr = float(os.getenv("MAX_RISK_INR", "250"))
    if max_risk_inr <= 0:
        raise ValueError("MAX_RISK_INR must be greater than 0")

    top_n = args.top if args.top is not None else int(os.getenv("TOP_N", "3"))

    all_qualifying, errors = scan_market(NIFTY_100_TICKERS, max_risk_inr=max_risk_inr, top_n=None)
    if top_n > 0:
        setups = all_qualifying[:top_n]
        runner_ups = all_qualifying[top_n:]
    else:
        setups = all_qualifying
        runner_ups = []

    scan_date = setups[0].date if setups else datetime.now(IST).strftime("%Y-%m-%d")

    message = format_telegram_message(
        setups=setups,
        scan_date=scan_date,
        max_risk_inr=max_risk_inr,
        scanned_count=len(NIFTY_100_TICKERS),
        error_count=len(errors),
        runner_ups=runner_ups,
    )

    if errors:
        print(f"[WARN] {len(errors)} ticker(s) failed to download or had insufficient data.")

    if args.dry_run:
        print("=== DRY RUN (No Telegram sent, trades_log.csv untouched) ===")
        print(message)
        return 0

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if (
        not bot_token
        or not chat_id
        or bot_token == "your_bot_token_here"
        or chat_id == "your_chat_id_here"
    ):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to real values in .env (or use --dry-run)."
        )
    if not chat_id.lstrip("-").isdigit() and not chat_id.startswith("@"):
        raise RuntimeError(
            f"Invalid TELEGRAM_CHAT_ID '{chat_id}'. Use your numeric Telegram user ID "
            "(not the bot username). Open your bot in Telegram, click Start, then get your numeric ID from @userinfobot or getUpdates."
        )

    send_telegram_message(bot_token, chat_id, message)
    written = append_signals([s.to_log_dict() for s in setups], log_path=BASE_DIR / "trades_log.csv")
    print(f"Sent Telegram alert for {len(setups)} setup(s). Logged {written} row(s) to trades_log.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

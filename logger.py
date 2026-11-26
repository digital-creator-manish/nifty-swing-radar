"""CSV trade signal logger for paper-trading tracking."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

CSV_FIELDS = [
    "date",
    "symbol",
    "entry",
    "stop_loss",
    "target",
    "qty",
    "capital_required",
    "vol_ratio",
    "status",
]


def append_signals(
    signals: Iterable[Mapping[str, object]],
    log_path: str | Path = "trades_log.csv",
) -> int:
    """Append triggered swing signals to trades_log.csv. Returns number of rows written."""
    rows = list(signals)
    if not rows:
        return 0

    path = Path(log_path)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for sig in rows:
            writer.writerow(
                {
                    "date": sig["date"],
                    "symbol": sig["symbol"],
                    "entry": f"{float(sig['entry']):.2f}",
                    "stop_loss": f"{float(sig['stop_loss']):.2f}",
                    "target": f"{float(sig['target']):.2f}",
                    "qty": int(sig["qty"]),
                    "capital_required": f"{float(sig['capital_required']):.2f}",
                    "vol_ratio": f"{float(sig['vol_ratio']):.2f}",
                    "status": sig.get("status", "OPEN"),
                }
            )
    return len(rows)

"""Explicit synthetic fixtures for software checks. Never a market-data fallback."""
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from core.models import SymbolInfo
from data.contracts import MarketDataContract


def diagnostic_fixture(output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp("2024-01-15", tz="UTC")
    end = start + pd.Timedelta(hours=6)
    contract = MarketDataContract("constant-price software fixture", "synthetic", calendar="24/7")
    frames = {}
    for tf, freq in (("1h", "1h"), ("15m", "15min"), ("5m", "5min")):
        index = pd.date_range(start - pd.Timedelta(hours=220), end, freq=freq, inclusive="left")
        frame = pd.DataFrame({"open": 100., "high": 100.5, "low": 99.5, "close": 100., "volume": 1.}, index=index)
        frames[tf] = contract.normalize(frame)
        frame.to_csv(root / f"{tf}.csv", index_label="timestamp")
    info = SymbolInfo("FIXTURE", 1, 1, 1, 1, 100, 1, leverage=10)
    (root / "symbol.json").write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")
    (root / "contract.json").write_text(json.dumps(asdict(contract), indent=2), encoding="utf-8")
    (root / "NOTICE.txt").write_text("SYNTHETIC SOFTWARE FIXTURE. NOT MARKET DATA OR PROFITABILITY EVIDENCE.", encoding="utf-8")
    return frames, info, start, end

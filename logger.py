"""Structured console and rotating-file logging without credential fields."""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import config

SENSITIVE = {"password", "secret", "api_key", "token", "login"}
FIELDNAMES = [
    "timestamp", "symbol", "direction", "entry_price", "stop_loss", "take_profit",
    "size", "risk_amount", "rr_ratio", "exit_price", "raw_exit_price", "fees",
    "pnl", "pnl_pct", "result", "mode", "entry_order_id", "tp_order_id",
    "sl_order_id", "notes",
]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "event_type": getattr(record, "event_type", "application"),
            "mode": getattr(record, "mode", ""),
            "symbol": getattr(record, "symbol", ""),
            "signal_id": getattr(record, "signal_id", ""),
            "order_id": getattr(record, "order_id", ""),
            "position_id": getattr(record, "position_id", ""),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build(name: str, filename: str) -> logging.Logger:
    instance = logging.getLogger(name)
    if instance.handlers:
        return instance
    instance.setLevel(logging.INFO)
    Path("logs").mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        Path("logs") / filename, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    instance.addHandler(file_handler)
    return instance


_logs = {
    "application": _build("trading.application", "application.jsonl"),
    "trades": _build("trading.trades", "trades.jsonl"),
    "risk": _build("trading.risk", "risk-rejections.jsonl"),
    "broker": _build("trading.broker", "broker-execution.jsonl"),
    "critical": _build("trading.critical", "critical-incidents.jsonl"),
}


def event(message: str, *, channel: str = "application", level: int = logging.INFO, **context: Any) -> None:
    safe = {k: v for k, v in context.items() if k.lower() not in SENSITIVE}
    _logs[channel].log(level, message, extra=safe)


def info(msg: str) -> None:
    print(f"[INFO] {msg}")
    event(msg)


def success(msg: str) -> None:
    print(f"[OK] {msg}")
    event(msg)


def warning(msg: str) -> None:
    print(f"[WARN] {msg}")
    event(msg, level=logging.WARNING)


def error(msg: str) -> None:
    print(f"[ERROR] {msg}")
    event(msg, level=logging.ERROR)


def log_trade(trade: dict[str, Any]) -> None:
    path = Path(config.TRADE_LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if new:
            writer.writeheader()
        writer.writerow({name: trade.get(name, "") for name in FIELDNAMES})
    event("trade", channel="trades", **{k: trade.get(k, "") for k in ("mode", "symbol")})


def trade_signal(*args: Any, **kwargs: Any) -> None:
    if args:
        names = ("direction", "symbol", "entry", "sl", "tp", "size", "confidence")
        kwargs.update(dict(zip(names, args, strict=False)))
    info(
        f"{kwargs.get('direction')} {kwargs.get('symbol')} entry={kwargs.get('entry')} "
        f"SL={kwargs.get('sl')} TP={kwargs.get('tp')} size={kwargs.get('size')}"
    )


def session_summary(balance: float, trades: int, wins: int, pnl: float) -> None:
    info(f"Session: balance={balance:.2f}, trades={trades}, wins={wins}, pnl={pnl:+.2f}")

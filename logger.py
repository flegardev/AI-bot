"""
logger.py - Trade logging to CSV and colored console output.
"""
import csv
import os
from datetime import datetime

from colorama import Fore, Style, init

import config

init(autoreset=True)

FIELDNAMES = [
    "timestamp",
    "symbol",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "size",
    "risk_amount",
    "rr_ratio",
    "exit_price",
    "raw_exit_price",
    "fees",
    "pnl",
    "pnl_pct",
    "result",
    "mode",
    "entry_order_id",
    "tp_order_id",
    "sl_order_id",
    "notes",
]


def _ensure_log_file():
    """Create CSV with headers if it doesn't exist yet."""
    if not os.path.exists(config.TRADE_LOG_FILE):
        with open(config.TRADE_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def log_trade(trade: dict):
    """Append a trade row to the CSV log."""
    _ensure_log_file()
    row = {field: trade.get(field, "") for field in FIELDNAMES}
    with open(config.TRADE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)


def info(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.CYAN}[{ts}] INFO  {Style.RESET_ALL}{msg}")


def success(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.GREEN}[{ts}] OK    {Style.RESET_ALL}{msg}")


def warning(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.YELLOW}[{ts}] WARN  {Style.RESET_ALL}{msg}")


def error(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{Fore.RED}[{ts}] ERROR {Style.RESET_ALL}{msg}")


def trade_signal(direction: str, symbol: str, entry: float, sl: float,
                 tp: float, size: float, confidence: float = None):
    """Pretty-print a trade signal."""
    color = Fore.GREEN if direction == "LONG" else Fore.RED
    ts = datetime.now().strftime("%H:%M:%S")
    print()
    print(f"{color}{'-' * 55}")
    print(f"  TRADE SIGNAL  [{ts}]")
    print(f"{'-' * 55}{Style.RESET_ALL}")
    print(f"  Symbol    : {symbol}")
    print(f"  Direction : {color}{direction}{Style.RESET_ALL}")
    print(f"  Entry     : {entry:.4f}")
    print(f"  Stop Loss : {Fore.RED}{sl:.4f}{Style.RESET_ALL}")
    print(f"  Take Profit: {Fore.GREEN}{tp:.4f}{Style.RESET_ALL}")
    print(f"  Size      : {size:.6f}")
    if confidence is not None:
        conf_color = Fore.GREEN if confidence >= 0.70 else Fore.YELLOW
        print(f"  ML Conf   : {conf_color}{confidence:.1%}{Style.RESET_ALL}")
    print(f"{color}{'-' * 55}{Style.RESET_ALL}")
    print()


def session_summary(balance: float, trades: int, wins: int, pnl: float):
    """Print end-of-session summary."""
    win_rate = (wins / trades * 100) if trades > 0 else 0
    color = Fore.GREEN if pnl >= 0 else Fore.RED
    print()
    print(f"{Fore.CYAN}{'-' * 45}")
    print("  SESSION SUMMARY")
    print(f"{'-' * 45}{Style.RESET_ALL}")
    print(f"  Balance   : ${balance:,.2f}")
    print(f"  Trades    : {trades}")
    print(f"  Win Rate  : {win_rate:.1f}%")
    print(f"  PnL       : {color}${pnl:+,.2f}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'=' * 45}{Style.RESET_ALL}")
    print()

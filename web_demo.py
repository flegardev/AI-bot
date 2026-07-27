"""Read-only dashboard backed only by actual artifacts and persisted events."""
from __future__ import annotations

import csv
import json
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config
from persistence.store import TradingStore

HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))


def load_dashboard_data() -> dict:
    artifact_dir = Path(config.ARTIFACT_DIR)
    summary_path = artifact_dir / "backtest_summary.json"
    equity_path = artifact_dir / "equity_curve.csv"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
    equity = []
    if equity_path.exists():
        with equity_path.open(encoding="utf-8") as handle:
            equity = list(csv.DictReader(handle))[-500:]
    store = TradingStore(config.DATABASE_PATH)
    reconciliation = store.get_state("last_reconciliation")
    errors = store.recent_events(10, "critical_unprotected_position")
    risk_rejections = store.recent_events(20, "risk_rejection")
    paper_balance = store.get_state("paper_balance")
    paper_positions = store.get_state("paper_positions", [])
    mode_labels = {"backtest": "BACKTEST", "paper": "PAPER", "live": f"LIVE {config.SETTINGS.broker.upper()}"}
    return {
        "mode": mode_labels.get(config.SETTINGS.trading_mode, config.SETTINGS.trading_mode.upper()),
        "live": config.SETTINGS.trading_mode == "live",
        "backtest": summary,
        "equity_curve": equity,
        "paper": {
            "balance": paper_balance,
            "open_positions": paper_positions,
        },
        "last_reconciliation": reconciliation,
        "recent_execution_errors": errors,
        "blocked_trade_reasons": risk_rejections,
        "risk_limits": {
            "risk_per_trade": config.RISK_PER_TRADE,
            "max_daily_loss": config.MAX_DAILY_LOSS_PCT,
            "max_drawdown": config.MAX_DRAWDOWN_PCT,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
        },
    }


def page(data: dict) -> str:
    warning = (
        '<div class="warning">LIVE MODE — REAL MONEY MAY BE AT RISK</div>'
        if data["live"] else ""
    )
    summary = data["backtest"]
    if summary:
        metrics = "".join(
            f"<article><small>{escape(str(name).replace('_', ' '))}</small><strong>{escape(str(value))}</strong></article>"
            for name, value in summary.items()
            if name in {"final_balance", "net_return", "maximum_drawdown", "profit_factor", "total_trades"}
        )
    else:
        metrics = '<div class="empty">No data available.</div>'
    paper = data["paper"]
    account = (
        f"<article><small>Paper balance</small><strong>{paper['balance']}</strong></article>"
        if paper["balance"] is not None else '<div class="empty">No data available.</div>'
    )
    details = escape(json.dumps({
        "open_positions": paper["open_positions"],
        "last_reconciliation": data["last_reconciliation"],
        "risk_limits": data["risk_limits"],
        "blocked_trade_reasons": data["blocked_trade_reasons"],
        "recent_execution_errors": data["recent_execution_errors"],
    }, indent=2, default=str))
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Trading Bot Dashboard</title>
<style>
body{{font-family:Segoe UI,sans-serif;background:#071018;color:#edf6ff;margin:0;padding:32px}}
main{{max-width:1100px;margin:auto}} header{{display:flex;justify-content:space-between;align-items:center}}
.mode{{padding:8px 12px;border:1px solid #39d0b3;border-radius:999px;color:#7ff5df}}
.warning{{background:#8b1d1d;color:white;padding:18px;margin:20px 0;font-weight:800;text-align:center}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:18px 0}}
article,pre,.empty{{background:#101d28;border:1px solid #24394a;border-radius:8px;padding:18px}}
small{{display:block;color:#8ea8bb;text-transform:uppercase}}strong{{display:block;font-size:25px;margin-top:8px}}
pre{{overflow:auto;white-space:pre-wrap}}h2{{margin-top:30px}}.empty{{color:#9cb0bf}}
</style></head><body><main><header><div><h1>Trading Bot</h1><p>Actual persisted state and generated artifacts only.</p></div>
<span class="mode">{escape(data["mode"])}</span></header>{warning}
<h2>Backtest</h2><section class="grid">{metrics}</section>
<h2>Account</h2><section class="grid">{account}</section>
<h2>Operations</h2><pre>{details}</pre>
</main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        data = load_dashboard_data()
        path = urlparse(self.path).path
        if path == "/api/status":
            payload = json.dumps(data, indent=2, default=str).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/":
            payload = page(data).encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    print(f"Dashboard: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

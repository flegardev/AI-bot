"""
Local visual demo for the AI Trading Strategy Bot.

Runs a small HTTP dashboard backed by the real strategy and risk modules. It
uses deterministic local OHLCV data so it is safe to open without exchange
credentials, live orders, or network access.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pandas as pd

import config
from risk.manager import build_trade
from strategy.bias import get_bias_details
from strategy.entry import get_entry_details
from strategy.liquidity import detect_liquidity_sweep

HOST = "127.0.0.1"
PORT = 8765


def _ohlcv(rows: list[dict], freq: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2026-07-06 08:00", periods=len(df), freq=freq, tz="UTC")
    return df


def _bias_frame() -> pd.DataFrame:
    rows = []
    price = 61200.0
    for i in range(config.EMA_PERIOD + 60):
      price += 18 + (i % 9) * 1.4
      rows.append({
          "open": price - 24,
          "high": price + 42,
          "low": price - 58,
          "close": price,
          "volume": 180 + (i % 21) * 4,
      })
    return _ohlcv(rows, "1h")


def _liquidity_frame() -> pd.DataFrame:
    rows = [
        {"open": 65100.0, "high": 65320.0, "low": 64950.0, "close": 65160.0, "volume": 180.0}
        for _ in range(54)
    ]
    rows[8]["low"] = 64650.0
    rows[20]["low"] = 64685.0
    rows[36]["low"] = 64670.0
    rows[-1] = {"open": 64850.0, "high": 65180.0, "low": 64480.0, "close": 64890.0, "volume": 390.0}
    return _ohlcv(rows, "15min")


def _entry_frame() -> pd.DataFrame:
    rows = [
        {"open": 64900.0, "high": 65100.0, "low": 64760.0, "close": 64940.0, "volume": 230.0}
        for _ in range(36)
    ]
    rows[6]["high"] = 65320.0
    rows[14]["low"] = 64580.0
    rows[22]["high"] = 65280.0
    rows[-1] = {"open": 65120.0, "high": 65690.0, "low": 65080.0, "close": 65580.0, "volume": 520.0}
    return _ohlcv(rows, "5min")


def evaluate_demo() -> dict:
    df_1h = _bias_frame()
    df_15m = _liquidity_frame()
    df_5m = _entry_frame()

    bias = get_bias_details(df_1h)
    sweep = detect_liquidity_sweep(df_15m)
    entry = get_entry_details(df_5m, sweep["direction"] or "bullish")
    direction = "LONG" if sweep["direction"] == "bullish" else "SHORT"
    trade = build_trade(
        direction=direction,
        entry_price=float(entry["current_price"]),
        liquidity_level=float(sweep["level"] or df_15m["low"].iloc[-1]),
        atr=float(entry["atr_14"]),
        account_balance=config.ACCOUNT_BALANCE,
    )

    equity = [10000, 10095, 9940, 10170, 10320, 10260, 10540, 10785, 10690, 10980]
    trades = [
        {"setup": "Bullish sweep + BOS", "result": "win", "pnl": 195.0, "rr": 2.0},
        {"setup": "Bearish sweep failed confirmation", "result": "skip", "pnl": 0.0, "rr": 0.0},
        {"setup": "Bullish continuation", "result": "win", "pnl": 265.0, "rr": 2.4},
        {"setup": "Low R:R filter", "result": "skip", "pnl": 0.0, "rr": 1.3},
        {"setup": "Bullish sweep invalidated", "result": "loss", "pnl": -100.0, "rr": 2.0},
    ]
    wins = sum(1 for trade_row in trades if trade_row["result"] == "win")
    losses = sum(1 for trade_row in trades if trade_row["result"] == "loss")
    closed = wins + losses

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "symbol": config.SYMBOL,
        "mode": "local-demo",
        "bias": bias,
        "sweep": sweep,
        "entry": entry,
        "trade": trade,
        "equity": equity,
        "trades": trades,
        "summary": {
            "balance": equity[-1],
            "netPnl": round(equity[-1] - equity[0], 2),
            "winRate": round((wins / closed) * 100, 1) if closed else 0,
            "maxDrawdown": 1.5,
            "closedTrades": closed,
            "filteredSetups": sum(1 for trade_row in trades if trade_row["result"] == "skip"),
            "riskAmount": round(config.ACCOUNT_BALANCE * config.RISK_PER_TRADE, 2),
            "startingBalance": equity[0],
        },
    }


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _result_class(result: str) -> str:
    return {"win": "good", "loss": "danger", "skip": "warn"}.get(result, "muted")


def _sparkline(values: list[float]) -> str:
    width = 760
    height = 180
    min_v = min(values)
    max_v = max(values)
    span = max(max_v - min_v, 1)
    points = []
    for idx, value in enumerate(values):
        x = (idx / (len(values) - 1)) * width
        y = height - ((value - min_v) / span) * height
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _layout(title: str, active: str, body: str) -> str:
    links = [("Dashboard", "/"), ("Strategy", "/strategy"), ("Backtest", "/backtest"), ("API", "/api/evaluate")]
    nav = "".join(
        f'<a class="{"active" if label == active else ""}" href="{href}"'
        f'{" aria-current=\"page\"" if label == active else ""}>{label}</a>'
        for label, href in links
    )
    active_label = escape(active)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #081015;
      --shell: #0b141b;
      --panel: #101b24;
      --panel-2: #142331;
      --panel-3: #0d1821;
      --line: #253849;
      --line-soft: #1b2a38;
      --text: #f3f7fb;
      --muted: #99acbf;
      --muted-2: #688096;
      --accent: #2dd4bf;
      --accent-2: #7dd3fc;
      --warn: #f59e0b;
      --danger: #ef4444;
      --good: #22c55e;
      --shadow: 0 18px 70px rgba(0, 0, 0, .25);
    }}
    * {{ box-sizing: border-box; }}
    .skip-link {{ position: fixed; left: 16px; top: 12px; z-index: 10; padding: 10px 14px; border-radius: 8px; background: white; color: #081015; transform: translateY(-180%); }}
    .skip-link:focus {{ transform: translateY(0); }}
    a:focus-visible {{ outline: 3px solid var(--accent-2); outline-offset: 3px; }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
    body {{
      margin: 0;
      min-height: 100dvh;
      background:
        radial-gradient(circle at 18% 0%, rgba(45, 212, 191, .10), transparent 28rem),
        linear-gradient(180deg, #0a151d 0%, var(--bg) 44%, #070c11 100%);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .shell {{ display: grid; grid-template-columns: 268px minmax(0, 1fr); min-height: 100dvh; }}
    aside {{ border-right: 1px solid var(--line-soft); background: rgba(9, 18, 25, .88); padding: 28px 22px; position: sticky; top: 0; height: 100dvh; }}
    .brand-row {{ display: flex; align-items: center; gap: 12px; }}
    .mark {{ display: grid; place-items: center; width: 42px; height: 42px; border: 1px solid rgba(45, 212, 191, .45); border-radius: 10px; background: rgba(45, 212, 191, .10); color: var(--accent); font-weight: 900; }}
    .brand {{ font-size: 19px; font-weight: 800; letter-spacing: .01em; }}
    .tag {{ color: var(--muted); font-size: 13px; margin-top: 14px; line-height: 1.55; }}
    nav {{ display: grid; gap: 8px; margin-top: 34px; }}
    nav a {{ color: #d6e2ed; text-decoration: none; padding: 12px 14px; border-radius: 8px; border: 1px solid transparent; transition: background .16s ease, border-color .16s ease, transform .16s ease; }}
    nav a.active, nav a:hover {{ background: #122233; border-color: #25445c; color: white; }}
    nav a:active {{ transform: translateY(1px); }}
    .side-panel {{ margin-top: 34px; padding: 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: var(--panel-3); }}
    .side-panel strong {{ display: block; font-size: 13px; margin-bottom: 6px; }}
    main {{ padding: 34px; }}
    .top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; line-height: 1.08; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; line-height: 1.25; }}
    p {{ color: var(--muted); line-height: 1.6; margin: 0; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
    .pill {{ display: inline-flex; align-items: center; gap: 8px; border: 1px solid #25445c; background: #0f1d2a; border-radius: 999px; padding: 8px 12px; color: #c2d8eb; font-size: 13px; white-space: nowrap; }}
    .pill.accent {{ border-color: rgba(45, 212, 191, .45); background: rgba(45, 212, 191, .10); color: #c7fff7; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }}
    .grid.two {{ grid-template-columns: minmax(0, 1.22fr) minmax(360px, .78fr); align-items: stretch; }}
    .grid.three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .stack {{ display: grid; gap: 16px; }}
    .card {{ background: linear-gradient(180deg, rgba(18, 31, 43, .98), rgba(13, 23, 32, .98)); border: 1px solid var(--line); border-radius: 8px; padding: 20px; box-shadow: var(--shadow); }}
    .card.subtle {{ box-shadow: none; background: var(--panel); }}
    .metric-label {{ color: var(--muted-2); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .metric-value {{ font-size: 29px; line-height: 1.05; font-weight: 850; margin-top: 10px; }}
    .metric-note {{ margin-top: 10px; font-size: 13px; color: var(--muted); }}
    .muted {{ color: var(--muted); }}
    .accent {{ color: var(--accent); }}
    .good {{ color: var(--good); }}
    .danger {{ color: var(--danger); }}
    .warn {{ color: var(--warn); }}
    .split-line {{ display: flex; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--line-soft); }}
    .split-line:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .split-line b {{ font-weight: 750; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 13px 10px; text-align: left; font-size: 14px; }}
    th {{ color: #9fb8d5; font-size: 12px; text-transform: uppercase; letter-spacing: .07em; }}
    td:last-child, th:last-child {{ text-align: right; }}
    .status {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 750; text-transform: capitalize; }}
    .status.good {{ background: rgba(34, 197, 94, .12); color: #86efac; }}
    .status.warn {{ background: rgba(245, 158, 11, .13); color: #fcd34d; }}
    .status.danger {{ background: rgba(239, 68, 68, .13); color: #fca5a5; }}
    code, pre {{ font-family: "Cascadia Code", ui-monospace, SFMono-Regular, Menlo, monospace; }}
    pre {{ white-space: pre-wrap; color: #c8d8ea; background: #09131c; border: 1px solid var(--line); border-radius: 8px; padding: 16px; overflow: auto; max-height: 430px; }}
    .chart {{ width: 100%; height: 256px; border: 1px solid var(--line); border-radius: 8px; background: linear-gradient(180deg, #0a141d, #081018); padding: 20px; margin-top: 12px; }}
    .bar {{ height: 10px; border-radius: 999px; background: #1f2a3b; overflow: hidden; margin-top: 12px; }}
    .bar span {{ display: block; height: 100%; background: var(--accent); }}
    .empty {{ padding: 28px; text-align: center; color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; background: rgba(255, 255, 255, .02); }}
    .section-gap {{ margin-top: 16px; }}
    @media (max-width: 900px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; }}
      main {{ padding: 22px; }}
      .top {{ display: grid; }}
      .pill-row {{ justify-content: flex-start; }}
      .grid, .grid.two, .grid.three {{ grid-template-columns: 1fr; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ scroll-behavior: auto !important; transition: none !important; }}
    }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <div class="shell">
    <aside>
      <div class="brand-row"><div class="mark">AI</div><div class="brand">AI Strategy Bot</div></div>
      <div class="tag">Local research dashboard backed by the Python strategy modules.</div>
      <nav aria-label="Demo sections">{nav}</nav>
      <div class="side-panel">
        <strong>Viewing {active_label}</strong>
        <p>Safe local demo. Uses deterministic candles, no API keys, and no exchange orders.</p>
      </div>
    </aside>
    <main id="main-content" tabindex="-1">{body}</main>
  </div>
</body>
</html>"""


def page_dashboard(data: dict) -> str:
    summary = data["summary"]
    trade = data["trade"] or {}
    start = summary["startingBalance"]
    balance_change = ((summary["balance"] - start) / start) * 100 if start else 0
    body = f"""
      <div class="top">
        <div>
          <h1>Trading Strategy Control Room</h1>
          <p>Demo mode evaluates deterministic candles through the real bias, liquidity, entry, and risk modules.</p>
        </div>
        <div class="pill-row">
          <div class="pill accent">{data["symbol"]}</div>
          <div class="pill">Paper safe</div>
          <div class="pill">No exchange keys</div>
        </div>
      </div>
      <section class="grid">
        <div class="card"><div class="metric-label">Bias</div><div class="metric-value accent">{data["bias"]["bias"]}</div><div class="metric-note">1H price is above EMA 200.</div></div>
        <div class="card"><div class="metric-label">Liquidity Sweep</div><div class="metric-value good">{data["sweep"]["direction"].title()}</div><div class="metric-note">15m equal lows swept and reclaimed.</div></div>
        <div class="card"><div class="metric-label">Entry Signal</div><div class="metric-value">{data["entry"]["type"]}</div><div class="metric-note">5m structure confirms continuation.</div></div>
        <div class="card"><div class="metric-label">Demo Balance</div><div class="metric-value">{_money(summary["balance"])}</div><div class="metric-note good">{balance_change:+.1f}% from start</div></div>
      </section>
      <section class="grid two section-gap">
        <div class="card">
          <h2>Equity Curve</h2>
          <p>The curve uses local sample trades so the dashboard works offline and stays repeatable.</p>
          <svg class="chart" viewBox="0 0 800 220" role="img" aria-label="Demo equity curve">
            <polyline fill="none" stroke="#2dd4bf" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="{_sparkline(data["equity"])}" transform="translate(20 20)" />
          </svg>
        </div>
        <div class="stack">
          <div class="card">
            <div class="metric-label">Current Trade Plan</div>
            <div class="metric-value">{trade.get("direction", "N/A")}</div>
            <div class="split-line"><span>Entry</span><b>{_money(trade.get("entry", 0))}</b></div>
            <div class="split-line"><span>Stop loss</span><b>{_money(trade.get("stop_loss", 0))}</b></div>
            <div class="split-line"><span>Take profit</span><b>{_money(trade.get("take_profit", 0))}</b></div>
            <div class="split-line"><span>Risk amount</span><b>{_money(summary["riskAmount"])}</b></div>
          </div>
          <div class="card subtle">
            <div class="metric-label">Closed Trade Win Rate</div>
            <div class="metric-value">{summary["winRate"]}%</div>
            <div class="bar"><span style="width:{summary["winRate"]}%"></span></div>
            <p class="metric-note">{summary["closedTrades"]} closed trades, {summary["filteredSetups"]} setups filtered.</p>
          </div>
        </div>
      </section>
      <section class="grid three section-gap">
        <div class="card subtle"><div class="metric-label">Guardrail</div><div class="metric-value">{config.MAX_DAILY_LOSS_PCT:.0%}</div><p class="metric-note">Daily loss cap before new entries stop.</p></div>
        <div class="card subtle"><div class="metric-label">Risk Per Trade</div><div class="metric-value">{config.RISK_PER_TRADE:.0%}</div><p class="metric-note">Position sizing is based on account balance.</p></div>
        <div class="card subtle"><div class="metric-label">Minimum R:R</div><div class="metric-value">{config.MIN_RISK_REWARD:.1f}:1</div><p class="metric-note">Trades below the threshold are skipped.</p></div>
      </section>
    """
    return _layout("AI Strategy Bot Dashboard", "Dashboard", body)


def page_strategy(data: dict) -> str:
    module_output = json.dumps(
        {"bias": data["bias"], "sweep": data["sweep"], "entry": data["entry"], "trade": data["trade"]},
        indent=2,
        default=_json_default,
    )
    body = f"""
      <div class="top">
        <div>
          <h1>Strategy Signal Breakdown</h1>
          <p>Multi-timeframe decision chain from 1H bias to 15m liquidity sweep to 5m entry confirmation.</p>
        </div>
        <div class="pill-row">
          <div class="pill accent">Risk per trade {config.RISK_PER_TRADE:.0%}</div>
          <div class="pill">EMA {config.EMA_PERIOD}</div>
        </div>
      </div>
      <section class="grid two">
        <div class="card">
          <h2>Signal Path</h2>
          <table>
            <caption class="sr-only">Strategy evaluation layers and evidence</caption>
            <thead><tr><th>Layer</th><th>Result</th><th>Evidence</th></tr></thead>
            <tbody>
              <tr><td>1H bias</td><td class="accent">{data["bias"]["bias"]}</td><td>Price {_money(data["bias"]["price"])} vs EMA200 {_money(data["bias"]["ema_200"])}</td></tr>
              <tr><td>15m sweep</td><td class="good">{data["sweep"]["direction"].title()}</td><td>Liquidity level {_money(data["sweep"]["level"])}</td></tr>
              <tr><td>5m entry</td><td>{data["entry"]["type"]}</td><td>RSI {data["entry"]["rsi_14"]:.1f}, ATR {data["entry"]["atr_14"]:.2f}</td></tr>
              <tr><td>Risk manager</td><td class="accent">{data["trade"]["rr_ratio"]}:1 R:R</td><td>Position size {data["trade"]["size"]:,.5f}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="card">
          <h2>Raw Module Output</h2>
          <pre>{escape(module_output)}</pre>
        </div>
      </section>
      <section class="grid three section-gap">
        <div class="card subtle"><div class="metric-label">Sweep Direction</div><div class="metric-value good">{data["sweep"]["direction"].title()}</div><p class="metric-note">A reclaimed low suggests long-side liquidity was taken.</p></div>
        <div class="card subtle"><div class="metric-label">ATR Percent</div><div class="metric-value">{data["entry"]["atr_pct"]:.2%}</div><p class="metric-note">Used as buffer around the swept level.</p></div>
        <div class="card subtle"><div class="metric-label">Position Size</div><div class="metric-value">{data["trade"]["size"]:,.5f}</div><p class="metric-note">Calculated from fixed account risk.</p></div>
      </section>
    """
    return _layout("AI Strategy Bot Signal Breakdown", "Strategy", body)


def page_backtest(data: dict) -> str:
    rows = "".join(
        f'<tr><td>{escape(item["setup"])}</td><td><span class="status {_result_class(item["result"])}">{item["result"]}</span></td><td>{_money(item["pnl"])}</td><td>{item["rr"]}</td></tr>'
        for item in data["trades"]
    ) or '<tr><td colspan="4"><div class="empty">No demo trades are available yet.</div></td></tr>'
    summary = data["summary"]
    body = f"""
      <div class="top">
        <div>
          <h1>Backtest Demo Report</h1>
          <p>A deterministic local run that demonstrates how the strategy filters setups and reports risk-adjusted outcomes.</p>
        </div>
        <div class="pill-row">
          <div class="pill accent">Generated {data["generatedAt"][:10]}</div>
          <div class="pill">Local sample</div>
        </div>
      </div>
      <section class="grid">
        <div class="card"><div class="metric-label">Net P&L</div><div class="metric-value good">{_money(summary["netPnl"])}</div><div class="metric-note">Sample equity gain.</div></div>
        <div class="card"><div class="metric-label">Win Rate</div><div class="metric-value">{summary["winRate"]}%</div><div class="metric-note">{summary["closedTrades"]} closed trades.</div></div>
        <div class="card"><div class="metric-label">Max Drawdown</div><div class="metric-value warn">{summary["maxDrawdown"]}%</div><div class="metric-note">Peak-to-trough sample drawdown.</div></div>
        <div class="card"><div class="metric-label">Filtered Setups</div><div class="metric-value">{summary["filteredSetups"]}</div><div class="metric-note">Skipped by strategy or risk filters.</div></div>
      </section>
      <section class="card section-gap">
        <h2>Trade Decisions</h2>
        <table>
          <caption class="sr-only">Deterministic demo trade decisions</caption>
          <thead><tr><th>Setup</th><th>Decision</th><th>P&L</th><th>R:R</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
      <section class="grid three section-gap">
        <div class="card subtle"><div class="metric-label">Mode</div><div class="metric-value">Demo</div><p class="metric-note">No live exchange connectivity required.</p></div>
        <div class="card subtle"><div class="metric-label">Fees</div><div class="metric-value">{config.BACKTEST_FEE_RATE:.2%}</div><p class="metric-note">Configured backtest fee assumption.</p></div>
        <div class="card subtle"><div class="metric-label">Slippage</div><div class="metric-value">{config.BACKTEST_SLIPPAGE_PCT:.2%}</div><p class="metric-note">Configured execution slippage assumption.</p></div>
      </section>
    """
    return _layout("AI Strategy Bot Backtest Report", "Backtest", body)


class Handler(BaseHTTPRequestHandler):
    def _common_headers(self, content_type: str, content_length: int = 0) -> None:
        self.send_header("Content-Type", content_type)
        if content_length:
            self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _send(self, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = content.encode("utf-8")
        self.send_response(200)
        self._common_headers(content_type, len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._common_headers("text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        data = evaluate_demo()
        path = urlparse(self.path).path
        if path == "/":
            self._send(page_dashboard(data))
        elif path == "/strategy":
            self._send(page_strategy(data))
        elif path == "/backtest":
            self._send(page_backtest(data))
        elif path == "/api/evaluate":
            self._send(json.dumps(data, indent=2, default=_json_default), "application/json; charset=utf-8")
        else:
            self.send_error(404, "Not found")

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web-demo] {self.address_string()} {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AI Strategy Bot web demo running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()

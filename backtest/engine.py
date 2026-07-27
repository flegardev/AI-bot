"""Temporally correct event-driven backtest using the shared strategy and paper broker."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config
import logger
from brokers.base import BrokerCapabilities
from brokers.paper import PaperBrokerAdapter
from core.models import OrderRequest, PositionSizeRequest, SymbolInfo
from core.strategy_engine import StrategyEngine
from core.timeframes import filter_closed_candles, timeframe_to_timedelta
from data.fetcher import fetch_historical_range
from persistence.store import TradingStore
from risk.engine import RiskEngine, RiskLimits


def _get_slice(
    df: pd.DataFrame, as_of: pd.Timestamp, timeframe: str = "5m", min_bars: int = 10
) -> pd.DataFrame | None:
    """Compatibility helper that exposes only candles closed by ``as_of``."""
    sliced = filter_closed_candles(df, timeframe, as_of)
    return sliced if len(sliced) >= min_bars else None


class BacktestEngine:
    def __init__(
        self,
        symbol: str | None = None,
        balance: float | None = None,
        artifact_dir: str | Path | None = None,
        capabilities: BrokerCapabilities | None = None,
        symbol_info: SymbolInfo | None = None,
    ):
        self.symbol = symbol or config.SYMBOL
        self.initial_balance = balance or config.INITIAL_BALANCE
        self.balance = self.initial_balance
        self.artifact_dir = Path(artifact_dir or config.ARTIFACT_DIR)
        self.capabilities = capabilities or BrokerCapabilities()
        self.symbol_info = symbol_info or SymbolInfo(
            self.symbol, 0.00001, 1.0, 100_000.0, 0.01, 100.0, 0.01, leverage=100
        )
        self.strategy = StrategyEngine()
        self.trades: list[dict[str, Any]] = []
        self.rejections: dict[str, int] = {}
        self.equity_rows: list[dict[str, Any]] = []
        self.exposure_bars = 0

    def _download_data(self) -> dict[str, pd.DataFrame]:
        return {
            tf: fetch_historical_range(
                self.symbol, tf, config.BACKTEST_START, config.BACKTEST_END
            )
            for tf in (config.TF_BIAS, config.TF_LIQUIDITY, config.TF_ENTRY)
        }

    def run(
        self,
        train_ml: bool = False,
        data: dict[str, pd.DataFrame] | None = None,
    ) -> dict[str, Any]:
        frames = data or self._download_data()
        entry_frame = frames[config.TF_ENTRY]
        if entry_frame.empty:
            result = {"trades": [], "stats": {"message": "No data available."}}
            self._write_artifacts(result, frames)
            return result

        with tempfile.TemporaryDirectory() as temp_dir:
            store = TradingStore(Path(temp_dir) / "backtest.sqlite3")
            broker = PaperBrokerAdapter(
                store, self.initial_balance, self.symbol_info,
                spread=config.BACKTEST_SPREAD,
                commission_per_lot=config.BACKTEST_FEE_RATE * self.symbol_info.contract_size,
                slippage=config.BACKTEST_SLIPPAGE_PCT,
            )
            broker.connect()
            latency_bars = max(1, config.BACKTEST_LATENCY_BARS)
            pending: list[tuple[int, Any]] = []
            risk_engine = RiskEngine(RiskLimits(
                risk_per_trade=config.RISK_PER_TRADE,
                max_daily_loss_fraction=config.MAX_DAILY_LOSS_PCT,
                max_drawdown_fraction=config.MAX_DRAWDOWN_PCT,
                max_open_positions=config.MAX_OPEN_POSITIONS,
                max_portfolio_risk_fraction=config.SETTINGS.max_portfolio_risk_pct,
                max_symbol_exposure_fraction=config.SETTINGS.max_symbol_exposure_pct,
                max_price_age_seconds=10**12,
            ))
            equity_high_water = self.initial_balance
            day_start_equity = self.initial_balance
            current_day = None

            for index, (open_value, bar) in enumerate(entry_frame.iterrows()):
                open_time = pd.Timestamp(str(open_value))
                as_of = open_time + timeframe_to_timedelta(config.TF_ENTRY)
                fills = broker.process_bar(self.symbol, bar, as_of.to_pydatetime())
                account_after_bar = broker.get_account_state()
                equity_high_water = max(equity_high_water, account_after_bar.equity)
                if as_of.date() != current_day:
                    current_day = as_of.date()
                    day_start_equity = account_after_bar.equity
                for fill in fills:
                    events = store.recent_events(10, "position_closed")
                    match = next(
                        (event for event in events if event["entity_id"] == fill.position_id), None
                    )
                    payload = match["payload"] if match else {}
                    trade = {
                        "position_id": fill.position_id,
                        "symbol": fill.symbol,
                        "direction": fill.direction.value,
                        "entry_timestamp": payload.get("opened_at"),
                        "entry_price": payload.get("entry_price"),
                        "stop_loss": payload.get("stop_loss"),
                        "take_profit": payload.get("take_profit"),
                        "volume": payload.get("volume", fill.volume),
                        "exit_timestamp": fill.timestamp.isoformat(),
                        "exit_price": fill.price,
                        "fees": fill.commission,
                        "slippage": fill.slippage,
                        "pnl": float(payload.get("pnl", 0.0)),
                        "result": payload.get("reason", "closed"),
                    }
                    self.trades.append(trade)

                for scheduled_index, signal in list(pending):
                    if scheduled_index > index:
                        continue
                    pending.remove((scheduled_index, signal))
                    account = broker.get_account_state()
                    info = broker.get_symbol_info(signal.symbol)
                    sizing = broker.calculate_position_size(
                        sizing_request := PositionSizeRequest(
                            signal.symbol, signal.direction, signal.entry, signal.stop_loss,
                            config.RISK_PER_TRADE, account, info
                        )
                    )
                    decision = risk_engine.evaluate(
                        signal, sizing_request, broker.get_open_positions(),
                        equity_high_water=equity_high_water,
                        start_of_day_equity=day_start_equity,
                        price_timestamp=signal.timestamp,
                        sizing=sizing,
                    )
                    if not decision.approved:
                        reason = "; ".join(decision.reasons)
                        self.rejections[reason] = self.rejections.get(reason, 0) + 1
                        continue
                    request = OrderRequest(signal, sizing.volume, f"bt-{signal.signal_id}")
                    order_result = broker.place_order(request)
                    if order_result.status.value not in {"filled", "partially_filled"}:
                        reason = order_result.message or "broker rejection"
                        self.rejections[reason] = self.rejections.get(reason, 0) + 1

                if not broker.get_open_positions() and not pending:
                    tail_limits = {
                        config.TF_BIAS: config.EMA_PERIOD + 20,
                        config.TF_LIQUIDITY: config.LIQUIDITY_LOOKBACK + 10,
                        config.TF_ENTRY: max(config.STRUCTURE_LOOKBACK, 20) + 10,
                    }
                    candles = {}
                    for timeframe, frame in frames.items():
                        cutoff = as_of - timeframe_to_timedelta(timeframe)
                        closed_index = frame.index.searchsorted(cutoff, side="right")
                        candles[timeframe] = frame.iloc[
                            max(0, closed_index - tail_limits[timeframe]):closed_index
                        ]
                    signal = self.strategy.evaluate(
                        self.symbol, candles, as_of,
                        can_long=self.capabilities.long, can_short=self.capabilities.short
                    )
                    if signal:
                        pending.append((index + latency_bars, signal))
                if broker.get_open_positions():
                    self.exposure_bars += 1
                account = broker.get_account_state()
                self.equity_rows.append({
                    "timestamp": as_of.isoformat(),
                    "balance": account.balance,
                    "equity": account.equity,
                })

            for position in list(broker.get_open_positions()):
                last = entry_frame.iloc[-1]
                fill = broker._close_at(
                    position, float(last["close"]), "end_of_data",
                    (entry_frame.index[-1] + timeframe_to_timedelta(config.TF_ENTRY)).to_pydatetime(),
                )
                event = store.recent_events(1, "position_closed")[0]["payload"]
                self.trades.append({
                    "position_id": fill.position_id, "symbol": fill.symbol,
                    "direction": fill.direction.value,
                    "entry_timestamp": event.get("opened_at"),
                    "entry_price": event.get("entry_price"),
                    "stop_loss": event.get("stop_loss"),
                    "take_profit": event.get("take_profit"),
                    "volume": event.get("volume", fill.volume),
                    "exit_timestamp": fill.timestamp.isoformat(),
                    "exit_price": fill.price, "fees": fill.commission, "slippage": fill.slippage,
                    "pnl": float(event.get("pnl", 0.0)), "result": "end_of_data",
                })
            self.balance = broker.balance
            broker.disconnect()

        stats = self._compute_stats(entry_frame)
        output: dict[str, Any] = {
            "trades": self.trades, "stats": stats, "rejections": self.rejections
        }
        self._write_artifacts(output, frames)
        if train_ml and self.trades:
            logger.warning("ML training requires feature-complete trade records; no model was changed.")
        return output

    def _compute_stats(self, entry_frame: pd.DataFrame) -> dict[str, Any]:
        pnls = np.array([float(trade["pnl"]) for trade in self.trades], dtype=float)
        positive = pnls[pnls > 0]
        negative = pnls[pnls < 0]
        equity = pd.Series([row["equity"] for row in self.equity_rows], dtype=float)
        if equity.empty:
            equity = pd.Series([self.initial_balance, self.balance], dtype=float)
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max.replace(0, np.nan)
        returns = equity.pct_change().dropna()
        downside = returns[returns < 0]
        duration_days = max(
            (entry_frame.index[-1] - entry_frame.index[0]).total_seconds() / 86400, 1
        )
        net_return = self.balance / self.initial_balance - 1
        annualized = (1 + net_return) ** (365 / duration_days) - 1 if net_return > -1 else -1
        sharpe = (
            float(returns.mean() / returns.std(ddof=1) * math.sqrt(365 * 24 * 12))
            if len(returns) > 1 and returns.std(ddof=1) > 0 else 0.0
        )
        sortino = (
            float(returns.mean() / downside.std(ddof=1) * math.sqrt(365 * 24 * 12))
            if len(downside) > 1 and downside.std(ddof=1) > 0 else 0.0
        )
        max_dd = abs(float(drawdown.min())) if len(drawdown) else 0.0
        wins = int((pnls > 0).sum())
        avg_win = float(positive.mean()) if len(positive) else 0.0
        avg_loss = float(negative.mean()) if len(negative) else 0.0
        by_direction = {
            side: round(sum(t["pnl"] for t in self.trades if t["direction"] == side), 4)
            for side in ("LONG", "SHORT")
        }
        return {
            "initial_balance": self.initial_balance,
            "final_balance": round(self.balance, 4),
            "net_return": net_return,
            "annualized_return": annualized,
            "maximum_drawdown": max_dd,
            "profit_factor": float(positive.sum() / abs(negative.sum())) if len(negative) and negative.sum() else None,
            "expectancy_per_trade": float(pnls.mean()) if len(pnls) else 0.0,
            "win_rate": wins / len(pnls) if len(pnls) else 0.0,
            "average_win": avg_win,
            "average_loss": avg_loss,
            "payoff_ratio": avg_win / abs(avg_loss) if avg_loss else None,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": annualized / max_dd if max_dd else None,
            "exposure_time": self.exposure_bars / max(len(entry_frame), 1),
            "total_fees": sum(float(t["fees"]) for t in self.trades),
            "total_slippage": sum(float(t["slippage"]) for t in self.trades),
            "total_trades": len(self.trades),
            "long_short_results": by_direction,
            "rejected_signals": sum(self.rejections.values()),
            "rejection_reasons": self.rejections,
            "assumptions": {
                "spread": config.BACKTEST_SPREAD,
                "commission_rate": config.BACKTEST_FEE_RATE,
                "slippage": config.BACKTEST_SLIPPAGE_PCT,
                "latency_bars": config.BACKTEST_LATENCY_BARS,
                "simultaneous_sl_tp": "stop_first",
                "gap_policy": "fill_at_adverse_open",
            },
        }

    def _write_artifacts(self, result: dict[str, Any], frames: dict[str, pd.DataFrame]) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.artifact_dir / "backtest_summary.json").write_text(
            json.dumps(result["stats"], indent=2, default=str), encoding="utf-8"
        )
        pd.DataFrame(self.trades).to_csv(self.artifact_dir / "trades.csv", index=False)
        equity = pd.DataFrame(self.equity_rows)
        equity.to_csv(self.artifact_dir / "equity_curve.csv", index=False)
        if not equity.empty:
            values = equity["equity"]
            drawdown = (values - values.cummax()) / values.cummax()
            pd.DataFrame({"timestamp": equity["timestamp"], "drawdown": drawdown}).to_csv(
                self.artifact_dir / "drawdown_curve.csv", index=False
            )
            dated = equity.copy()
            dated["timestamp"] = pd.to_datetime(dated["timestamp"], utc=True)
            monthly = dated.set_index("timestamp")["equity"].resample("ME").last().pct_change()
            monthly.rename("return").to_csv(self.artifact_dir / "monthly_returns.csv")
        else:
            pd.DataFrame(columns=["timestamp", "drawdown"]).to_csv(
                self.artifact_dir / "drawdown_curve.csv", index=False
            )
            pd.DataFrame(columns=["timestamp", "return"]).to_csv(
                self.artifact_dir / "monthly_returns.csv", index=False
            )
        snapshot = {
            "symbol": self.symbol, "start": config.BACKTEST_START, "end": config.BACKTEST_END,
            "initial_balance": self.initial_balance, "risk_per_trade": config.RISK_PER_TRADE,
            **result["stats"].get("assumptions", {}),
        }
        (self.artifact_dir / "backtest_config_snapshot.json").write_text(
            json.dumps(snapshot, indent=2), encoding="utf-8"
        )

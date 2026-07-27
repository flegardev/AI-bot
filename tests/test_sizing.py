import pytest

from core.models import AccountState, Direction, PositionSizeRequest, SymbolInfo
from risk.sizing import calculate_mt5_position_size


def request(info, *, equity=10_000, free=10_000, entry=1.10, stop=1.09, risk=0.01):
    return PositionSizeRequest(
        info.symbol, Direction.LONG, entry, stop, risk,
        AccountState(equity, equity, free, equity - free), info
    )


@pytest.mark.parametrize(
    "info,entry,stop",
    [
        (SymbolInfo("EURUSD", 0.00001, 1.0, 100_000, 0.01, 100, 0.01, leverage=100), 1.10, 1.09),
        (SymbolInfo("XAUUSD", 0.01, 1.0, 100, 0.01, 50, 0.01, leverage=50), 2400, 2390),
        (SymbolInfo("US500", 0.1, 1.0, 1, 0.1, 100, 0.1, leverage=20), 5500, 5480),
        (SymbolInfo("ODD", 0.25, 7.5, 10, 0.25, 20, 0.25, leverage=10), 100, 95),
    ],
)
def test_symbol_types_risk_never_exceeds_limit(info, entry, stop):
    result = calculate_mt5_position_size(request(info, entry=entry, stop=stop, free=1_000_000))
    assert result.accepted
    assert result.estimated_loss_at_stop <= result.requested_risk


def test_volume_rounds_down_to_step():
    info = SymbolInfo("X", 1, 10, 1, 0.1, 100, 0.1, leverage=1000)
    result = calculate_mt5_position_size(request(info, entry=100, stop=97, free=100_000))
    assert result.volume == 3.3


def test_minimum_volume_is_rejected_not_rounded_up():
    info = SymbolInfo("X", 1, 100, 1, 1.0, 100, 1.0, leverage=100)
    result = calculate_mt5_position_size(request(info, equity=100, entry=100, stop=90, risk=0.01))
    assert not result.accepted
    assert "minimum volume" in result.rejection_reason


def test_insufficient_margin_rejected():
    info = SymbolInfo("X", 1, 1, 1000, 0.1, 100, 0.1, leverage=1)
    result = calculate_mt5_position_size(request(info, entry=100, stop=99, free=50))
    assert result.rejection_reason == "insufficient free margin"


def test_zero_and_wrong_side_stops_rejected():
    info = SymbolInfo("X", 0.01, 1, 1, 0.01, 100, 0.01)
    assert not calculate_mt5_position_size(request(info, entry=100, stop=100)).accepted
    assert not calculate_mt5_position_size(request(info, entry=100, stop=101)).accepted


def test_tight_stop_below_broker_minimum_rejected():
    info = SymbolInfo("X", 0.01, 1, 1, 0.01, 100, 0.01, stops_level_points=10, point=0.01)
    result = calculate_mt5_position_size(request(info, entry=100, stop=99.95))
    assert result.rejection_reason == "stop violates broker minimum distance"


def test_broker_profit_and_margin_calculators_are_used():
    info = SymbolInfo("X", 1, 1, 1, 0.1, 100, 0.1)
    result = calculate_mt5_position_size(
        request(info, entry=100, stop=90, free=1000),
        profit_calculator=lambda entry, stop: -250,
        margin_calculator=lambda volume: 25 * volume,
    )
    assert result.volume == 0.4
    assert result.estimated_margin == 10

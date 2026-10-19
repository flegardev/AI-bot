import pytest

from backtest.references import exposure_matched
from core.models import SymbolInfo


@pytest.mark.parametrize("change", [5., -5.])
def test_direction_references_match_times_volume_currency_and_costs(change):
    info = SymbolInfo("FIXTURE", .1, 2., 1., 1., 100., 1.)
    trades = [{"entry_midpoint": 100., "exit_midpoint": 100. + change, "entry_spread": .2,
               "exit_spread": .4, "volume": 2., "fees": 3., "swap": 1.}]
    report = exposure_matched(trades, info, .001)
    factor = 2 * 2 / .1
    costs = (.3 + (200 + change) * .001) * factor + 4
    assert report["net_pnl"]["always_long"] == pytest.approx(change * factor - costs)
    assert report["net_pnl"]["always_short"] == pytest.approx(-change * factor - costs)
    assert report["net_pnl"]["flat"] == 0
    assert report["matched_intervals"] == 1

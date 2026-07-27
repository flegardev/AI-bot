from datetime import datetime, timezone

from brokers.mt5 import MT5BrokerAdapter
from core.models import Direction, OrderRequest, OrderStatus, TradeSignal


class FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_H1 = 1, 5, 15, 60
    TIMEFRAME_H4, TIMEFRAME_D1 = 240, 1440
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    POSITION_TYPE_BUY = 0
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 2, 3
    TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10009, 10010
    ORDER_TIME_GTC, ORDER_FILLING_IOC = 0, 1
    SYMBOL_TRADE_MODE_DISABLED = 0

    def __init__(self):
        self.sent = []
        self.position = None

    def initialize(self, **kwargs): return True
    def login(self, *args, **kwargs): return True
    def shutdown(self): return True
    def last_error(self): return (0, "ok")
    def terminal_info(self): return {"connected": True}
    def account_info(self):
        values = dict(balance=10000, equity=9900, margin_free=9000, margin=900,
                      profit=-100, currency="USD", leverage=100)
        return type("Account", (), values)()
    def symbol_select(self, symbol, visible): return True
    def symbol_info(self, symbol):
        values = dict(visible=True, trade_tick_size=0.00001, point=0.00001,
                      trade_tick_value_loss=1, trade_tick_value=1,
                      trade_contract_size=100000, volume_min=0.01, volume_max=100,
                      volume_step=0.01, trade_stops_level=10, currency_profit="USD",
                      trade_mode=1)
        return type("Symbol", (), values)()
    def symbol_info_tick(self, symbol):
        return type("Tick", (), {"ask": 1.1001, "bid": 1.0999, "_asdict": lambda self: {"ask": self.ask, "bid": self.bid}})()
    def order_calc_profit(self, order_type, symbol, volume, entry, stop): return -1000
    def order_calc_margin(self, order_type, symbol, volume, entry): return volume * 1000
    def order_check(self, request): return type("Check", (), {"retcode": 0})()
    def order_send(self, request):
        self.sent.append(request)
        return type("Result", (), {
            "retcode": self.TRADE_RETCODE_DONE, "order": 11, "deal": 22,
            "volume": request.get("volume", 1), "price": request.get("price", 1.1),
            "comment": "done", "_asdict": lambda self: {"retcode": self.retcode}
        })()
    def positions_get(self, **kwargs): return ()
    def orders_get(self, **kwargs): return ()


def test_mt5_account_uses_equity_and_free_margin():
    adapter = MT5BrokerAdapter(FakeMT5())
    adapter.connect()
    account = adapter.get_account_state()
    assert account.balance == 10000
    assert account.equity == 9900
    assert account.free_margin == 9000
    assert account.floating_pnl == -100


def test_mt5_initial_order_contains_sl_tp_and_magic():
    fake = FakeMT5()
    adapter = MT5BrokerAdapter(fake)
    adapter.connect()
    signal = TradeSignal(
        "EURUSD", Direction.LONG, 1.1, 1.09, 1.12, datetime.now(timezone.utc), "test"
    )
    result = adapter.place_order(OrderRequest(signal, 0.1, "stable-id", max_slippage=10))
    assert result.status == OrderStatus.FILLED
    assert fake.sent[0]["sl"] == 1.09
    assert fake.sent[0]["tp"] == 1.12
    assert fake.sent[0]["comment"] == "stable-id"

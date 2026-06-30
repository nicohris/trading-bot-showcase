"""
Tests unitaires des modèles de données core.

Ces tests vérifient la logique des propriétés calculées
et s'assurent que les modèles se comportent comme attendu.
"""

from datetime import datetime, timezone

import pytest

from core.enums import OrderSide, OrderStatus, OrderType, SignalType
from core.models import AccountSnapshot, Candle, Order, Position, Signal
from core.utils import utcnow
from tests.conftest import make_candle, make_signal


class TestCandle:
    def test_is_bullish_when_close_above_open(self):
        c = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=39000, high=41000, low=38500, close=40500, volume=100
        )
        assert c.is_bullish is True

    def test_is_bearish_when_close_below_open(self):
        c = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=40500, high=41000, low=38500, close=39000, volume=100
        )
        assert c.is_bullish is False

    def test_range_size(self):
        c = make_candle(close=40000.0, high=41000.0, low=39000.0)
        assert c.range_size == pytest.approx(2000.0)

    def test_body_size(self):
        c = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=39000, high=41000, low=38500, close=40000, volume=100
        )
        assert c.body_size == pytest.approx(1000.0)

    def test_frozen_immutable(self):
        c = make_candle()
        with pytest.raises((AttributeError, TypeError)):
            c.close = 99999  # type: ignore


class TestSignal:
    def test_is_entry_for_buy_signals(self):
        for signal_type in (SignalType.BUY_BREAKOUT, SignalType.BUY_PULLBACK):
            signal = make_signal(signal_type=signal_type)
            assert signal.is_entry is True

    def test_is_exit_for_close_signals(self):
        for signal_type in (SignalType.CLOSE_PARTIAL, SignalType.CLOSE_ALL):
            signal = make_signal(signal_type=signal_type)
            assert signal.is_exit is True

    def test_is_none_for_none_signal(self):
        signal = make_signal(signal_type=SignalType.NONE)
        assert signal.is_none is True
        assert signal.is_entry is False


class TestPosition:
    def test_unrealized_pnl_long(self):
        pos = Position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.1,
            entry_price=40000.0,
            current_price=41000.0,
        )
        assert pos.unrealized_pnl == pytest.approx(100.0)  # 0.1 * 1000

    def test_unrealized_pnl_long_negative(self):
        pos = Position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.1,
            entry_price=40000.0,
            current_price=39000.0,
        )
        assert pos.unrealized_pnl == pytest.approx(-100.0)

    def test_market_value(self):
        pos = Position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.5,
            entry_price=40000.0,
            current_price=42000.0,
        )
        assert pos.market_value == pytest.approx(21000.0)


class TestAccountSnapshot:
    def test_exposure_pct(self):
        pos = Position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=0.25,
            entry_price=40000.0,
            current_price=40000.0,
        )
        account = AccountSnapshot(
            timestamp=utcnow(),
            total_equity=10000.0,
            available_balance=0.0,
            open_positions=[pos],
        )
        # 0.25 * 40000 = 10000, 10000/10000 = 100%
        assert account.exposure_pct == pytest.approx(100.0)

    def test_no_positions_exposure(self):
        account = AccountSnapshot(
            timestamp=utcnow(),
            total_equity=10000.0,
            available_balance=10000.0,
            open_positions=[],
        )
        assert account.exposure_pct == 0.0
        assert account.total_exposure == 0.0

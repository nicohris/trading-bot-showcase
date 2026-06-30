"""
Fixtures pytest partagées entre tous les tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.enums import OrderSide, SignalType
from core.models import AccountSnapshot, Candle, Position, Signal


def make_candle(
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    close: float = 40000.0,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
    timestamp: datetime | None = None,
) -> Candle:
    """Factory pour créer des Candle de test facilement."""
    ts = timestamp or datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=ts,
        open=close * 0.999,
        high=high or close * 1.002,
        low=low or close * 0.998,
        close=close,
        volume=volume,
    )


def make_account_snapshot(
    equity: float = 10000.0,
    available: float = 10000.0,
    positions: list | None = None,
) -> AccountSnapshot:
    """Factory pour AccountSnapshot de test."""
    from core.utils import utcnow
    return AccountSnapshot(
        timestamp=utcnow(),
        total_equity=equity,
        available_balance=available,
        open_positions=positions or [],
    )


def make_signal(
    symbol: str = "BTCUSDT",
    signal_type: SignalType = SignalType.BUY_BREAKOUT,
    close_price: float = 40000.0,
    atr: float = 500.0,
) -> Signal:
    """Factory pour Signal de test."""
    from core.utils import utcnow
    return Signal(
        signal_type=signal_type,
        symbol=symbol,
        timeframe="1h",
        timestamp=utcnow(),
        close_price=close_price,
        atr=atr,
        reason="Test signal",
    )


@pytest.fixture
def sample_candle() -> Candle:
    return make_candle()


@pytest.fixture
def sample_account() -> AccountSnapshot:
    return make_account_snapshot()


@pytest.fixture
def sample_buy_signal() -> Signal:
    return make_signal()

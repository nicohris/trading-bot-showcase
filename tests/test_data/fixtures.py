"""
Factories de données de test partagées entre les tests du module data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.models import Candle


def make_candles(
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    count: int = 100,
    start: datetime | None = None,
    interval_hours: int = 1,
    base_price: float = 40000.0,
    with_volume: float = 1000.0,
) -> list[Candle]:
    """
    Génère une série de bougies synthétiques propres (sans trous, sans doublons).

    Les prix varient légèrement autour de base_price pour être réalistes.
    """
    if start is None:
        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    candles = []
    for i in range(count):
        ts = start + timedelta(hours=interval_hours * i)
        price = base_price + i * 10  # Légère tendance haussière
        candle = Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=price * 0.999,
            high=price * 1.002,
            low=price * 0.998,
            close=price,
            volume=with_volume,
        )
        candles.append(candle)
    return candles


def make_candles_4h(
    symbol: str = "BTCUSDT",
    count: int = 100,
    start: datetime | None = None,
    base_price: float = 40000.0,
) -> list[Candle]:
    return make_candles(
        symbol=symbol, timeframe="4h", count=count,
        start=start, interval_hours=4, base_price=base_price,
    )


def make_raw_kline(ts_ms: int, price: float = 40000.0) -> list:
    """Simule une kline brute Binance (format liste de 12 éléments)."""
    return [
        ts_ms,               # open_time
        str(price * 0.999),  # open
        str(price * 1.002),  # high
        str(price * 0.998),  # low
        str(price),          # close
        "1000.0",            # volume
        ts_ms + 3599999,     # close_time
        "40000000.0",        # quote_asset_volume
        "500",               # number_of_trades
        "500.0",             # taker_buy_base
        "20000000.0",        # taker_buy_quote
        "0",                 # ignore
    ]

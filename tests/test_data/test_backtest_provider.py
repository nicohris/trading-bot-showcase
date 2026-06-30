"""
Tests du BacktestDataProvider — walk-forward, multi-timeframe, pas de lookahead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data.fetcher import BacktestDataProvider
from tests.test_data.fixtures import make_candles, make_candles_4h


@pytest.fixture
def provider_1h_4h():
    """Provider chargé avec des données 1h et 4h alignées."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles_1h = make_candles(timeframe="1h", count=200, start=start, interval_hours=1)
    candles_4h = make_candles_4h(count=50, start=start)

    provider = BacktestDataProvider(exec_timeframe="1h")
    provider.load("BTCUSDT", "1h", candles_1h)
    provider.load("BTCUSDT", "4h", candles_4h)
    return provider, candles_1h, candles_4h


class TestBacktestDataProviderLoad:
    def test_load_deduplicates(self):
        candles = make_candles(count=10)
        # Ajouter des doublons
        candles_with_dups = candles + candles[:3]

        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", candles_with_dups)

        # Le provider doit avoir dédupliqué
        loaded = provider._data["BTCUSDT"]["1h"]
        timestamps = [c.timestamp for c in loaded]
        assert len(timestamps) == len(set(timestamps))

    def test_load_sorts_chronologically(self):
        candles = make_candles(count=10)
        # Mélanger l'ordre
        import random
        shuffled = candles.copy()
        random.shuffle(shuffled)

        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", shuffled)

        loaded = provider._data["BTCUSDT"]["1h"]
        for i in range(1, len(loaded)):
            assert loaded[i].timestamp > loaded[i - 1].timestamp


class TestBacktestDataProviderAdvance:
    def test_advance_returns_false_when_exhausted(self):
        candles = make_candles(count=5)
        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", candles)

        count = 0
        while provider.advance("BTCUSDT"):
            count += 1

        # On doit avoir consommé count bougies (toutes sauf la dernière)
        assert count == len(candles) - 1

    def test_advance_unknown_symbol_returns_false(self):
        provider = BacktestDataProvider()
        assert provider.advance("UNKNOWN") is False

    def test_cursor_starts_at_zero(self):
        candles = make_candles(count=10)
        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", candles)
        assert provider._cursor["BTCUSDT"] == 0


class TestBacktestDataProviderGetCandles:
    def test_no_candles_visible_before_first_advance(self, provider_1h_4h):
        provider, candles_1h, _ = provider_1h_4h
        result = provider.get_candles("BTCUSDT", "1h", limit=300)
        # Avant le premier advance, curseur = 0, rien de visible
        assert len(result) == 0

    def test_one_candle_visible_after_first_advance(self, provider_1h_4h):
        provider, candles_1h, _ = provider_1h_4h
        provider.advance("BTCUSDT")
        result = provider.get_candles("BTCUSDT", "1h", limit=300)
        assert len(result) == 1
        assert result[0].timestamp == candles_1h[0].timestamp

    def test_limit_respected(self, provider_1h_4h):
        provider, candles_1h, _ = provider_1h_4h
        # Avancer 50 fois
        for _ in range(50):
            provider.advance("BTCUSDT")

        result = provider.get_candles("BTCUSDT", "1h", limit=20)
        assert len(result) == 20

    def test_no_lookahead_bias(self, provider_1h_4h):
        """Les bougies futures ne doivent jamais être visibles."""
        provider, candles_1h, _ = provider_1h_4h

        for i in range(10):
            provider.advance("BTCUSDT")
            visible = provider.get_candles("BTCUSDT", "1h", limit=300)
            # La dernière bougie visible doit être candles_1h[i]
            assert visible[-1].timestamp == candles_1h[i].timestamp
            # La bougie candles_1h[i+1] ne doit pas être visible
            if i + 1 < len(candles_1h):
                assert all(c.timestamp != candles_1h[i + 1].timestamp for c in visible)


class TestBacktestDataProviderMultiTimeframe:
    def test_4h_filtered_by_current_timestamp(self, provider_1h_4h):
        """Le 4h retourne uniquement les bougies <= timestamp 1h courant."""
        provider, candles_1h, candles_4h = provider_1h_4h

        # Avancer jusqu'à la 5e bougie 1h (timestamp = start + 4h)
        for _ in range(5):
            provider.advance("BTCUSDT")

        current_ts = provider._get_current_exec_ts("BTCUSDT")
        candles_4h_visible = provider.get_candles("BTCUSDT", "4h", limit=300)

        # Toutes les bougies 4h visibles doivent avoir un timestamp <= current_ts
        assert all(c.timestamp <= current_ts for c in candles_4h_visible)

    def test_4h_no_lookahead(self, provider_1h_4h):
        """Aucune bougie 4h future ne doit être visible."""
        provider, candles_1h, candles_4h = provider_1h_4h

        # Avancer de 2 bougies 1h seulement
        provider.advance("BTCUSDT")
        provider.advance("BTCUSDT")

        current_ts = provider._get_current_exec_ts("BTCUSDT")
        candles_4h_visible = provider.get_candles("BTCUSDT", "4h", limit=300)

        # La première bougie 4h est à start (t=0), si current_ts < 4h, on ne doit en voir aucune
        # Dans notre cas start = même heure → la bougie 4h ts[0] == current_ts[1h] (start)
        # Après 2 avances : current_ts = start + 1h → la bougie 4h (start) est visible
        assert all(c.timestamp <= current_ts for c in candles_4h_visible)

    def test_get_latest_price_uses_exec_tf(self, provider_1h_4h):
        provider, candles_1h, _ = provider_1h_4h
        provider.advance("BTCUSDT")
        provider.advance("BTCUSDT")
        price = provider.get_latest_price("BTCUSDT")
        # Le prix doit correspondre à candles_1h[1].close (2ème avance → index 1)
        assert price == pytest.approx(candles_1h[1].close)


class TestBacktestDataProviderProgress:
    def test_progress_starts_at_zero(self):
        candles = make_candles(count=100)
        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", candles)
        current, total = provider.get_progress("BTCUSDT")
        assert current == 0
        assert total == 100

    def test_progress_increments_on_advance(self):
        candles = make_candles(count=100)
        provider = BacktestDataProvider()
        provider.load("BTCUSDT", "1h", candles)

        for _ in range(10):
            provider.advance("BTCUSDT")

        current, total = provider.get_progress("BTCUSDT")
        assert current == 10
        assert total == 100

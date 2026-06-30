"""
Tests du DataCache (lecture/écriture CSV).

Utilise un répertoire temporaire pour ne pas polluer le cache réel.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data.cache import DataCache
from tests.test_data.fixtures import make_candles


@pytest.fixture
def cache(tmp_path):
    """Cache pointant vers un dossier temporaire."""
    return DataCache(cache_dir=tmp_path)


class TestDataCacheExists:
    def test_not_exists_initially(self, cache):
        assert not cache.exists("BTCUSDT", "1h")

    def test_exists_after_save(self, cache):
        candles = make_candles(count=10)
        cache.save("BTCUSDT", "1h", candles)
        assert cache.exists("BTCUSDT", "1h")

    def test_different_symbol_not_exists(self, cache):
        candles = make_candles(symbol="BTCUSDT", count=10)
        cache.save("BTCUSDT", "1h", candles)
        assert not cache.exists("ETHUSDT", "1h")


class TestDataCacheSaveLoad:
    def test_save_and_load_roundtrip(self, cache):
        candles = make_candles(count=50)
        cache.save("BTCUSDT", "1h", candles)
        loaded = cache.load("BTCUSDT", "1h")

        assert len(loaded) == 50
        assert loaded[0].symbol == "BTCUSDT"
        assert loaded[0].timeframe == "1h"

    def test_timestamps_preserved(self, cache):
        candles = make_candles(count=5)
        cache.save("BTCUSDT", "1h", candles)
        loaded = cache.load("BTCUSDT", "1h")

        original_ts = [c.timestamp for c in candles]
        loaded_ts = [c.timestamp for c in loaded]
        assert original_ts == loaded_ts

    def test_prices_preserved(self, cache):
        candles = make_candles(count=5, base_price=50000.0)
        cache.save("BTCUSDT", "1h", candles)
        loaded = cache.load("BTCUSDT", "1h")

        for orig, load in zip(candles, loaded):
            assert orig.open == pytest.approx(load.open)
            assert orig.high == pytest.approx(load.high)
            assert orig.low == pytest.approx(load.low)
            assert orig.close == pytest.approx(load.close)
            assert orig.volume == pytest.approx(load.volume)

    def test_load_empty_cache_returns_empty(self, cache):
        result = cache.load("BTCUSDT", "1h")
        assert result == []

    def test_load_with_date_filter(self, cache):
        candles = make_candles(count=500, start=datetime(2024, 1, 1, tzinfo=timezone.utc))
        cache.save("BTCUSDT", "1h", candles)

        start_filter = datetime(2024, 1, 10, tzinfo=timezone.utc)
        end_filter = datetime(2024, 1, 20, tzinfo=timezone.utc)
        loaded = cache.load("BTCUSDT", "1h", start=start_filter, end=end_filter)

        assert all(c.timestamp >= start_filter for c in loaded)
        assert all(c.timestamp <= end_filter for c in loaded)
        assert len(loaded) > 0

    def test_loaded_candles_sorted(self, cache):
        """Les candles chargées doivent être triées chronologiquement."""
        candles = make_candles(count=20)
        cache.save("BTCUSDT", "1h", candles)
        loaded = cache.load("BTCUSDT", "1h")

        for i in range(1, len(loaded)):
            assert loaded[i].timestamp > loaded[i - 1].timestamp


class TestDataCacheMerge:
    def test_merge_new_data_with_existing(self, cache):
        """La sauvegarde successive doit merger sans doublons."""
        candles_first = make_candles(
            count=50, start=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        candles_second = make_candles(
            count=50, start=datetime(2024, 1, 3, tzinfo=timezone.utc)  # 48h plus tard
        )
        # Overlap : les 2 dernières de first chevauchent les 2 premières de second
        # (3h interval: on commence à j+48h avec interval=1h, overlap au point de jonction)

        cache.save("BTCUSDT", "1h", candles_first)
        cache.save("BTCUSDT", "1h", candles_second)
        loaded = cache.load("BTCUSDT", "1h")

        # Pas de doublons
        timestamps = [c.timestamp for c in loaded]
        assert len(timestamps) == len(set(timestamps))

        # Trié
        for i in range(1, len(loaded)):
            assert loaded[i].timestamp > loaded[i - 1].timestamp

    def test_save_empty_list_does_not_crash(self, cache):
        cache.save("BTCUSDT", "1h", [])  # Doit passer sans erreur
        assert not cache.exists("BTCUSDT", "1h")


class TestDataCacheCoverage:
    def test_coverage_none_when_no_cache(self, cache):
        assert cache.get_coverage("BTCUSDT", "1h") is None

    def test_coverage_matches_data(self, cache):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles = make_candles(count=24, start=start)  # 24 bougies 1h
        cache.save("BTCUSDT", "1h", candles)

        coverage = cache.get_coverage("BTCUSDT", "1h")
        assert coverage is not None
        assert coverage[0] == candles[0].timestamp
        assert coverage[1] == candles[-1].timestamp


class TestDataCacheListAndDelete:
    def test_list_cached(self, cache):
        cache.save("BTCUSDT", "1h", make_candles(symbol="BTCUSDT", timeframe="1h", count=5))
        cache.save("ETHUSDT", "4h", make_candles(symbol="ETHUSDT", timeframe="4h", count=5))

        listed = cache.list_cached()
        assert ("BTCUSDT", "1h") in listed
        assert ("ETHUSDT", "4h") in listed

    def test_delete(self, cache):
        cache.save("BTCUSDT", "1h", make_candles(count=5))
        assert cache.exists("BTCUSDT", "1h")
        deleted = cache.delete("BTCUSDT", "1h")
        assert deleted is True
        assert not cache.exists("BTCUSDT", "1h")

    def test_delete_nonexistent_returns_false(self, cache):
        assert cache.delete("BTCUSDT", "1h") is False

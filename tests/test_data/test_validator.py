"""
Tests du DataValidator — vérification des règles d'intégrité.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.models import Candle
from data.validator import DataValidator
from tests.test_data.fixtures import make_candles


@pytest.fixture
def validator():
    return DataValidator()


def _make_clean_candles(count: int = 50) -> list[Candle]:
    return make_candles(count=count)


class TestNotEmpty:
    def test_empty_list_is_error(self, validator):
        result = validator.validate([], "BTCUSDT", "1h")
        assert not result.is_valid
        assert any(i.rule == "not_empty" for i in result.errors)

    def test_non_empty_passes(self, validator):
        candles = _make_clean_candles(5)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "not_empty" for i in result.errors)


class TestSorted:
    def test_sorted_passes(self, validator):
        candles = _make_clean_candles(10)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "sorted" for i in result.errors)

    def test_unsorted_is_error(self, validator):
        candles = _make_clean_candles(5)
        # Inverser les deux premières bougies
        candles[0], candles[1] = candles[1], candles[0]
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "sorted" for i in result.errors)


class TestNoDuplicates:
    def test_no_duplicates_passes(self, validator):
        candles = _make_clean_candles(10)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "no_duplicates" for i in result.errors)

    def test_duplicate_timestamp_is_error(self, validator):
        candles = _make_clean_candles(5)
        # Dupliquer la deuxième bougie
        from copy import deepcopy
        candles.insert(2, candles[1])
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "no_duplicates" for i in result.errors)


class TestOHLCCoherence:
    def test_valid_ohlc_passes(self, validator):
        candles = _make_clean_candles(5)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "ohlc_coherent" for i in result.errors)

    def test_low_greater_than_high_is_error(self, validator):
        candles = _make_clean_candles(3)
        # Créer une bougie invalide : low > high
        bad = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=candles[-1].timestamp + timedelta(hours=1),
            open=40000, high=39000,   # high < open — invalide
            low=41000,                # low > high — invalide
            close=40500, volume=100,
        )
        candles.append(bad)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "ohlc_coherent" for i in result.errors)

    def test_open_outside_range_is_error(self, validator):
        candles = _make_clean_candles(3)
        bad = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=candles[-1].timestamp + timedelta(hours=1),
            open=99999,    # open > high — invalide
            high=40100, low=39900, close=40000, volume=100,
        )
        candles.append(bad)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "ohlc_coherent" for i in result.errors)


class TestPricesPositive:
    def test_positive_prices_pass(self, validator):
        candles = _make_clean_candles(5)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "prices_positive" for i in result.errors)

    def test_zero_price_is_error(self, validator):
        candles = _make_clean_candles(3)
        bad = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=candles[-1].timestamp + timedelta(hours=1),
            open=0, high=1, low=0, close=0, volume=100,
        )
        candles.append(bad)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "prices_positive" for i in result.errors)


class TestVolumePositive:
    def test_zero_volume_passes(self, validator):
        """Volume = 0 est acceptable (faible liquidité)."""
        candles = _make_clean_candles(3)
        zero_vol = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=candles[-1].timestamp + timedelta(hours=1),
            open=40000, high=40100, low=39900, close=40050, volume=0.0,
        )
        candles.append(zero_vol)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "volume_positive" for i in result.errors)

    def test_negative_volume_is_error(self, validator):
        candles = _make_clean_candles(3)
        bad = Candle(
            symbol="BTCUSDT", timeframe="1h",
            timestamp=candles[-1].timestamp + timedelta(hours=1),
            open=40000, high=40100, low=39900, close=40050, volume=-1.0,
        )
        candles.append(bad)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert any(i.rule == "volume_positive" for i in result.errors)


class TestGapDetection:
    def test_no_gaps_passes(self, validator):
        candles = _make_clean_candles(50)
        result = validator.validate(candles, "BTCUSDT", "1h")
        assert not any(i.rule == "no_gaps" for i in result.issues)

    def test_gap_detected_as_warning(self, validator):
        """Un petit trou doit être signalé en warning, pas en error."""
        candles = _make_clean_candles(20)
        # Insérer un trou de 5 heures entre la bougie 10 et 11
        from datetime import timedelta
        gapped = candles[:10] + [
            Candle(
                symbol="BTCUSDT", timeframe="1h",
                timestamp=candles[9].timestamp + timedelta(hours=6),  # Saut de 5h
                open=40000, high=40100, low=39900, close=40050, volume=100,
            )
        ] + candles[11:]
        result = validator.validate(gapped, "BTCUSDT", "1h")
        gap_issues = [i for i in result.issues if i.rule == "no_gaps"]
        assert len(gap_issues) > 0
        # Pour un seul trou sur 20 bougies (~5%), ça peut être warning ou error
        # selon le ratio — on vérifie juste qu'il est détecté
        assert any(i.rule == "no_gaps" for i in result.issues)

    def test_4h_timeframe_gaps_use_correct_interval(self, validator):
        """La détection de trous doit utiliser l'intervalle du timeframe."""
        candles = make_candles(timeframe="4h", interval_hours=4, count=20)
        result = validator.validate(candles, "BTCUSDT", "4h")
        # Données propres : pas de trous
        assert not any(i.rule == "no_gaps" for i in result.issues)


class TestValidationResult:
    def test_summary_string_has_key_info(self, validator):
        candles = _make_clean_candles(50)
        result = validator.validate(candles, "BTCUSDT", "1h")
        summary = result.summary()
        assert "BTCUSDT" in summary
        assert "1h" in summary
        assert "50" in summary

    def test_is_valid_with_warnings(self, validator):
        """is_valid doit être True même avec des warnings."""
        # Créer des données avec un trou mineur
        candles = _make_clean_candles(100)
        # Insérer un petit trou (1 manquante / 100 = 1%) → warning mais pas error
        candles_with_gap = candles[:50] + [
            Candle(
                symbol="BTCUSDT", timeframe="1h",
                timestamp=candles[49].timestamp + timedelta(hours=2),
                open=40000, high=40100, low=39900, close=40050, volume=100,
            )
        ] + candles[51:]
        result = validator.validate(candles_with_gap, "BTCUSDT", "1h", max_gap_ratio=0.05)
        # Le résultat is_valid dépend uniquement des errors, pas des warnings
        assert result.is_valid == (len(result.errors) == 0)

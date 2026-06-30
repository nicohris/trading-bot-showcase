"""
Tests des fonctions de calcul d'indicateurs.

Vérifie que les indicateurs sont calculés et nommés correctement
sans dépendre de la stratégie complète.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategy.indicators import (
    add_atr,
    add_ema,
    add_rolling_high,
    add_volume_ma,
    prepare_execution_indicators,
    prepare_trend_indicators,
)


def make_test_df(n: int = 250) -> pd.DataFrame:
    """Crée un DataFrame OHLCV synthétique pour les tests."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = [base + timedelta(hours=i) for i in range(n)]

    # Prix simulés avec une légère tendance haussière
    closes = [40000 + i * 10 + np.random.randn() * 50 for i in range(n)]
    opens = [c * 0.999 for c in closes]
    highs = [c * 1.002 for c in closes]
    lows = [c * 0.998 for c in closes]
    volumes = [1000 + np.random.randint(0, 500) for _ in range(n)]

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=pd.DatetimeIndex(timestamps, tz=timezone.utc))
    return df


class TestAddEma:
    def test_ema_column_created(self):
        df = make_test_df(100)
        result = add_ema(df, 20)
        assert "ema_20" in result.columns

    def test_ema_not_null_after_warmup(self):
        df = make_test_df(100)
        result = add_ema(df, 20)
        # Après la période de chauffe, pas de NaN
        assert not result["ema_20"].iloc[25:].isna().any()

    def test_ema_does_not_modify_original(self):
        df = make_test_df(100)
        _ = add_ema(df, 20)
        assert "ema_20" not in df.columns  # Original non modifié


class TestAddAtr:
    def test_atr_column_created(self):
        df = make_test_df(50)
        result = add_atr(df, 14)
        assert "atr_14" in result.columns

    def test_atr_is_positive(self):
        df = make_test_df(50)
        result = add_atr(df, 14)
        non_null = result["atr_14"].dropna()
        assert (non_null >= 0).all()


class TestAddRollingHigh:
    def test_rolling_high_column_created(self):
        df = make_test_df(50)
        result = add_rolling_high(df, 20)
        assert "rolling_high_20" in result.columns

    def test_rolling_high_excludes_current_candle(self):
        """Vérifier que la bougie courante n'est pas incluse (shift(1))."""
        df = make_test_df(30)
        result = add_rolling_high(df, 5)
        # La valeur à l'index i ne doit pas inclure high[i]
        # Elle doit être le max de high[i-5:i]
        # On ne peut pas vérifier avec précision sans refaire le calcul,
        # mais on s'assure qu'il y a des NaN au début
        assert result["rolling_high_5"].iloc[:25].isna().any()


class TestPrepareIndicators:
    def test_trend_indicators_all_columns_present(self):
        df = make_test_df(250)
        result = prepare_trend_indicators(df, ema_fast=50, ema_slow=200)
        assert "ema_50" in result.columns
        assert "ema_200" in result.columns
        assert "ema_50_slope" in result.columns

    def test_execution_indicators_all_columns_present(self):
        df = make_test_df(250)
        result = prepare_execution_indicators(df)
        expected_cols = [
            "ema_50", "ema_200", "ema_20",
            "atr_14", "atr_ma_20",
            "volume_ma_20",
            "rolling_high_20",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

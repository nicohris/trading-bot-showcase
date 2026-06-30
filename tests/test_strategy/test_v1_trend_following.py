"""
Tests unitaires de la stratégie TrendFollowingV1.

Approche : on mock les fonctions de calcul d'indicateurs pour tester
chaque règle de la stratégie en isolation, sans dépendance sur pandas-ta.

Structure des helpers :
  - make_trend_row()  → ligne DataFrame simulée pour le filtre 4h
  - make_exec_row()   → ligne DataFrame simulée pour le filtre 1h
  - make_exec_df()    → DataFrame 2 lignes (last + prev) pour les setups
  - make_context()    → StrategyContext minimal avec des Candle synthétiques
  - MockStrategy      → TrendFollowingV1 avec indicateurs pré-injectés
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.enums import SetupType, SignalType
from core.models import Candle, Signal
from strategy.base import StrategyContext
from strategy.v1_trend_following import TrendFollowingV1, _is_valid, _val


# ---------------------------------------------------------------------------
# Constantes de test (alignées avec trading_config.yaml par défaut)
# ---------------------------------------------------------------------------

EMA_FAST = 50
EMA_SLOW = 200
EMA_PB = 20
ATR_PERIOD = 14
ATR_MA_PERIOD = 20
VOL_MA_PERIOD = 20
LOOKBACK = 20
PROXIMITY = 0.5

# Noms de colonnes attendus
C_EMA_FAST = f"ema_{EMA_FAST}"
C_EMA_SLOW = f"ema_{EMA_SLOW}"
C_EMA_SLOPE = f"ema_{EMA_FAST}_slope"
C_EMA_PB = f"ema_{EMA_PB}"
C_ATR = f"atr_{ATR_PERIOD}"
C_ATR_MA = f"atr_ma_{ATR_MA_PERIOD}"
C_VOL_MA = f"volume_ma_{VOL_MA_PERIOD}"
C_ROLLING_HIGH = f"rolling_high_{LOOKBACK}"

# Valeurs de base cohérentes
BASE_CLOSE = 40000.0
BASE_ATR = 500.0
BASE_HIGH = 40200.0
BASE_LOW = 39800.0


# ---------------------------------------------------------------------------
# Builders de lignes DataFrame
# ---------------------------------------------------------------------------


def make_trend_row(
    ema_fast: float = 42000.0,  # > ema_slow → tendance haussière
    ema_slow: float = 40000.0,
    slope: float = 100.0,       # > 0 → pente positive
) -> pd.Series:
    """Ligne DataFrame 4h avec indicateurs de tendance."""
    return pd.Series({
        C_EMA_FAST: ema_fast,
        C_EMA_SLOW: ema_slow,
        C_EMA_SLOPE: slope,
    })


def make_exec_row(
    close: float = BASE_CLOSE,
    high: float = BASE_HIGH,
    low: float = BASE_LOW,
    volume: float = 2000.0,         # > volume_ma → participation OK
    ema_fast: float = 39500.0,      # < close → close > EMA50
    ema_slow: float = 38000.0,      # < ema_fast → EMA50 > EMA200
    ema_pb: float = 39700.0,        # EMA20
    atr: float = BASE_ATR,
    atr_ma: float = 400.0,          # < atr → volatilité OK
    volume_ma: float = 1500.0,      # < volume → participation OK
    rolling_high: float = 39500.0,  # < close → breakout possible
) -> pd.Series:
    """Ligne DataFrame 1h avec tous les indicateurs d'exécution."""
    return pd.Series({
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
        C_EMA_FAST: ema_fast,
        C_EMA_SLOW: ema_slow,
        C_EMA_PB: ema_pb,
        C_ATR: atr,
        C_ATR_MA: atr_ma,
        C_VOL_MA: volume_ma,
        C_ROLLING_HIGH: rolling_high,
    })


def make_exec_df(
    last: pd.Series | None = None,
    prev: pd.Series | None = None,
) -> pd.DataFrame:
    """
    DataFrame 2 lignes simulant les deux dernières bougies fermées.

    iloc[-1] = `last` (dernière fermée)
    iloc[-2] = `prev` (précédente)
    """
    last = last if last is not None else make_exec_row()
    prev = prev if prev is not None else make_exec_row(close=39900.0, high=39950.0)

    ts_prev = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    ts_last = ts_prev + timedelta(hours=1)
    return pd.DataFrame([prev, last], index=[ts_prev, ts_last])


def make_trend_df(row: pd.Series | None = None) -> pd.DataFrame:
    """DataFrame 1 ligne pour le timeframe 4h (on n'a besoin que du dernier)."""
    row = row if row is not None else make_trend_row()
    ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    return pd.DataFrame([row], index=[ts])


# ---------------------------------------------------------------------------
# Helper de contexte
# ---------------------------------------------------------------------------


def _make_candles(n: int, timeframe: str = "1h") -> list[Candle]:
    """Génère N bougies synthétiques pour satisfaire is_ready()."""
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    interval = timedelta(hours=1 if timeframe == "1h" else 4)
    candles = []
    price = 40000.0
    for i in range(n):
        candles.append(Candle(
            symbol="BTCUSDT",
            timeframe=timeframe,
            timestamp=start + interval * i,
            open=price * 0.999,
            high=price * 1.002,
            low=price * 0.998,
            close=price,
            volume=1000.0,
        ))
        price += 10  # légère hausse pour avoir EMA50 > EMA200 plus facilement
    return candles


def make_context(n_1h: int = 250, n_4h: int = 250) -> StrategyContext:
    return StrategyContext(
        symbol="BTCUSDT",
        exec_candles=_make_candles(n_1h, "1h"),
        trend_candles=_make_candles(n_4h, "4h"),
    )


# ---------------------------------------------------------------------------
# Fixture strategy avec mock des indicateurs
# ---------------------------------------------------------------------------


class MockStrategy(TrendFollowingV1):
    """
    TrendFollowingV1 où les fonctions d'indicateurs sont remplacées
    par des retours pré-définis.

    Permet de tester chaque règle de la stratégie indépendamment
    sans dépendance sur pandas-ta.
    """

    def __init__(
        self,
        trend_df: pd.DataFrame | None = None,
        exec_df: pd.DataFrame | None = None,
    ) -> None:
        super().__init__()
        self._mock_trend_df = trend_df if trend_df is not None else make_trend_df()
        self._mock_exec_df = exec_df if exec_df is not None else make_exec_df()

    def generate_signal(self, context: StrategyContext) -> Signal:
        """Override pour injecter les DataFrames mockés."""
        with patch(
            "strategy.v1_trend_following.prepare_trend_indicators",
            return_value=self._mock_trend_df,
        ), patch(
            "strategy.v1_trend_following.prepare_execution_indicators",
            return_value=self._mock_exec_df,
        ):
            return super().generate_signal(context)


# ---------------------------------------------------------------------------
# Tests helpers internes
# ---------------------------------------------------------------------------


class TestIsValid:
    def test_float_is_valid(self):
        assert _is_valid(40000.0) is True

    def test_nan_is_invalid(self):
        import math
        assert _is_valid(float("nan")) is False

    def test_inf_is_invalid(self):
        assert _is_valid(float("inf")) is False

    def test_none_is_invalid(self):
        assert _is_valid(None) is False

    def test_zero_is_valid(self):
        assert _is_valid(0.0) is True

    def test_negative_is_valid(self):
        assert _is_valid(-100.0) is True


class TestVal:
    def test_returns_float(self):
        row = pd.Series({"close": 40000.0})
        assert _val(row, "close") == pytest.approx(40000.0)

    def test_returns_none_for_missing_column(self):
        row = pd.Series({"close": 40000.0})
        assert _val(row, "nonexistent") is None

    def test_returns_none_for_nan(self):
        import math
        row = pd.Series({"close": float("nan")})
        assert _val(row, "close") is None


# ---------------------------------------------------------------------------
# Tests filtre tendance 4h
# ---------------------------------------------------------------------------


class TestTrend4h:
    def test_valid_trend_passes(self):
        strategy = TrendFollowingV1()
        row = make_trend_row(ema_fast=42000, ema_slow=40000, slope=100)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is True
        assert reason == ""

    def test_fails_when_ema_fast_below_slow(self):
        strategy = TrendFollowingV1()
        # EMA50 < EMA200 → downtrend
        row = make_trend_row(ema_fast=38000, ema_slow=40000, slope=100)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False
        assert "EMA" in reason

    def test_fails_when_ema_fast_equals_slow(self):
        strategy = TrendFollowingV1()
        row = make_trend_row(ema_fast=40000, ema_slow=40000, slope=100)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False

    def test_fails_when_slope_negative(self):
        strategy = TrendFollowingV1()
        # EMA50 > EMA200 mais pente négative → tendance s'affaiblit
        row = make_trend_row(ema_fast=42000, ema_slow=40000, slope=-50)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False
        assert "slope" in reason.lower()

    def test_fails_when_slope_zero(self):
        strategy = TrendFollowingV1()
        row = make_trend_row(ema_fast=42000, ema_slow=40000, slope=0)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False

    def test_fails_when_ema_nan(self):
        strategy = TrendFollowingV1()
        import math
        row = make_trend_row(ema_fast=float("nan"), ema_slow=40000, slope=100)
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False
        assert "NaN" in reason

    def test_fails_when_slope_nan(self):
        strategy = TrendFollowingV1()
        import math
        row = make_trend_row(ema_fast=42000, ema_slow=40000, slope=float("nan"))
        ok, reason = strategy._check_trend_4h(row)
        assert ok is False


# ---------------------------------------------------------------------------
# Tests filtre tendance 1h
# ---------------------------------------------------------------------------


class TestTrend1h:
    def test_valid_trend_passes(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=40000, ema_fast=39500, ema_slow=38000)
        ok, reason = strategy._check_trend_1h(row)
        assert ok is True

    def test_fails_when_ema_fast_below_slow(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=40000, ema_fast=37000, ema_slow=39000)
        ok, reason = strategy._check_trend_1h(row)
        assert ok is False
        assert "EMA" in reason

    def test_fails_when_close_below_ema_fast(self):
        strategy = TrendFollowingV1()
        # EMA50 > EMA200 mais le prix est sous EMA50
        row = make_exec_row(close=38000, ema_fast=39500, ema_slow=38000)
        ok, reason = strategy._check_trend_1h(row)
        assert ok is False
        assert "close" in reason.lower()

    def test_fails_when_close_equals_ema_fast(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=39500, ema_fast=39500, ema_slow=38000)
        ok, reason = strategy._check_trend_1h(row)
        assert ok is False

    def test_fails_when_nan(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=float("nan"), ema_fast=39500, ema_slow=38000)
        ok, reason = strategy._check_trend_1h(row)
        assert ok is False


# ---------------------------------------------------------------------------
# Tests filtre qualité
# ---------------------------------------------------------------------------


class TestQualityFilter:
    def test_valid_quality_passes(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(atr=500, atr_ma=400, volume=2000, volume_ma=1500)
        ok, reason = strategy._check_quality(row)
        assert ok is True

    def test_fails_when_atr_below_atr_ma(self):
        strategy = TrendFollowingV1()
        # ATR < ATR_MA → faible volatilité
        row = make_exec_row(atr=300, atr_ma=400, volume=2000, volume_ma=1500)
        ok, reason = strategy._check_quality(row)
        assert ok is False
        assert "ATR" in reason

    def test_fails_when_atr_equals_atr_ma(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(atr=400, atr_ma=400, volume=2000, volume_ma=1500)
        ok, reason = strategy._check_quality(row)
        assert ok is False

    def test_fails_when_volume_below_volume_ma(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(atr=500, atr_ma=400, volume=1000, volume_ma=1500)
        ok, reason = strategy._check_quality(row)
        assert ok is False
        assert "volume" in reason.lower()

    def test_fails_when_atr_nan(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(atr=float("nan"), atr_ma=400, volume=2000, volume_ma=1500)
        ok, reason = strategy._check_quality(row)
        assert ok is False


# ---------------------------------------------------------------------------
# Tests setup Breakout
# ---------------------------------------------------------------------------


class TestBreakout:
    def test_breakout_detected_when_close_above_rolling_high(self):
        strategy = TrendFollowingV1()
        # close=40500 > rolling_high=40000
        row = make_exec_row(close=40500, rolling_high=40000)
        ok, reason = strategy._check_breakout(row)
        assert ok is True
        assert "Breakout" in reason

    def test_no_breakout_when_close_equals_rolling_high(self):
        """close == rolling_high n'est PAS un breakout (condition stricte)."""
        strategy = TrendFollowingV1()
        row = make_exec_row(close=40000, rolling_high=40000)
        ok, reason = strategy._check_breakout(row)
        assert ok is False

    def test_no_breakout_when_close_below_rolling_high(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=39500, rolling_high=40000)
        ok, reason = strategy._check_breakout(row)
        assert ok is False

    def test_no_breakout_when_rolling_high_nan(self):
        strategy = TrendFollowingV1()
        row = make_exec_row(close=40500, rolling_high=float("nan"))
        ok, reason = strategy._check_breakout(row)
        assert ok is False

    def test_breakout_values_in_reason(self):
        """La raison doit contenir les valeurs pour les logs."""
        strategy = TrendFollowingV1()
        row = make_exec_row(close=40500.0, rolling_high=40000.0)
        ok, reason = strategy._check_breakout(row)
        assert "40500" in reason
        assert "40000" in reason


# ---------------------------------------------------------------------------
# Tests setup Pullback
# ---------------------------------------------------------------------------


class TestPullback:
    """
    Rappel des règles testées :
    1. close > prev_high
    2. |close - EMA20| ≤ 0.5×ATR  OU  |close - EMA50| ≤ 0.5×ATR
    """

    def _strategy(self):
        s = TrendFollowingV1()
        s._cfg = copy.copy(s._cfg)  # isoler du singleton partagé
        s._cfg.pullback_proximity_atr = PROXIMITY  # forcer 0.5 — V1 design
        return s

    def test_pullback_ema20_detected(self):
        """Pullback sur EMA20 : close proche EMA20 et close > prev_high."""
        strategy = self._strategy()
        atr = 500.0
        ema_pb = 39800.0
        close = 39850.0  # dist = 50 ≤ 0.5 × 500 = 250

        last = make_exec_row(close=close, ema_pb=ema_pb, ema_fast=39200.0, atr=atr)
        prev = make_exec_row(close=39700.0, high=39800.0)  # prev_high=39800

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is True
        assert setup == SetupType.PULLBACK_EMA20
        assert "EMA20" in reason or "Pullback" in reason

    def test_pullback_ema50_detected_when_ema20_not_near(self):
        """Pullback sur EMA50 quand EMA20 est trop loin."""
        strategy = self._strategy()
        atr = 500.0
        ema_fast = 39800.0
        ema_pb = 37000.0    # EMA20 trop loin
        close = 39850.0      # dist à EMA50 = 50 ≤ 250

        last = make_exec_row(close=close, ema_pb=ema_pb, ema_fast=ema_fast, atr=atr)
        prev = make_exec_row(close=39700.0, high=39800.0)

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is True
        assert setup == SetupType.PULLBACK_EMA50

    def test_pullback_ema20_takes_priority_over_ema50(self):
        """Quand les deux EMA sont dans la zone, EMA20 a la priorité."""
        strategy = self._strategy()
        atr = 500.0
        close = 39850.0
        ema_pb = 39820.0   # très proche (dist = 30 ≤ 250)
        ema_fast = 39830.0  # aussi proche

        last = make_exec_row(close=close, ema_pb=ema_pb, ema_fast=ema_fast, atr=atr)
        prev = make_exec_row(close=39700.0, high=39800.0)

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is True
        assert setup == SetupType.PULLBACK_EMA20  # EMA20 prioritaire

    def test_no_pullback_when_close_not_above_prev_high(self):
        """Condition 2 obligatoire : close doit dépasser le high précédent."""
        strategy = self._strategy()
        atr = 500.0
        close = 39850.0

        last = make_exec_row(close=close, ema_pb=39800.0, atr=atr)
        prev = make_exec_row(high=40000.0)  # prev_high > close → pas de breakout

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is False
        assert setup is None

    def test_no_pullback_when_close_equals_prev_high(self):
        """Condition stricte : close doit être STRICTEMENT > prev_high."""
        strategy = self._strategy()
        atr = 500.0
        close = 39850.0

        last = make_exec_row(close=close, ema_pb=39800.0, atr=atr)
        prev = make_exec_row(high=close)  # prev_high == close

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is False

    def test_no_pullback_when_far_from_both_emas(self):
        """Ni EMA20 ni EMA50 à portée → pas de pullback."""
        strategy = self._strategy()
        atr = 500.0
        close = 40000.0
        ema_pb = 37000.0    # dist = 3000 >> 250
        ema_fast = 37500.0  # dist = 2500 >> 250

        last = make_exec_row(close=close, ema_pb=ema_pb, ema_fast=ema_fast, atr=atr)
        prev = make_exec_row(high=39900.0)  # prev_high < close → condition 2 OK

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is False

    def test_pullback_proximity_boundary(self):
        """
        Test de la limite exacte de proximité.

        Avec proximity=0.5 et ATR=500, seuil = 250.
        dist = 250 → n'est PAS dans la zone (condition ≤ mais non strictement <).
        Vérifier la limite exacte.
        """
        strategy = self._strategy()
        atr = 500.0
        threshold = PROXIMITY * atr   # = 250.0
        close = 40000.0
        ema_pb = close - threshold    # distance EXACTE = 250 → dans la zone (≤)

        last = make_exec_row(close=close, ema_pb=ema_pb, atr=atr)
        prev = make_exec_row(high=39900.0)

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is True  # distance = seuil → condition respectée (≤)

    def test_no_pullback_when_distance_slightly_above_threshold(self):
        strategy = self._strategy()
        atr = 500.0
        threshold = PROXIMITY * atr   # 250.0
        close = 40000.0
        ema_pb = close - (threshold + 1.0)  # légèrement hors zone

        last = make_exec_row(close=close, ema_pb=ema_pb, ema_fast=ema_pb - 1000, atr=atr)
        prev = make_exec_row(high=39900.0)

        ok, reason, setup = strategy._check_pullback(last, prev, atr)
        assert ok is False


# ---------------------------------------------------------------------------
# Tests generate_signal — intégration via MockStrategy
# ---------------------------------------------------------------------------


class TestGenerateSignal:
    """
    Tests d'intégration de generate_signal.

    On mock les indicateurs pour contrôler précisément les conditions.
    On vérifie que le flow complet (filtres + setups) se comporte correctement.
    """

    def test_none_signal_when_not_enough_candles(self):
        """is_ready() doit court-circuiter si pas assez de bougies."""
        strategy = TrendFollowingV1()
        context = StrategyContext(
            symbol="BTCUSDT",
            exec_candles=_make_candles(10, "1h"),   # largement insuffisant
            trend_candles=_make_candles(10, "4h"),
        )
        signal = strategy.generate_signal(context)
        assert signal.signal_type == SignalType.NONE
        assert signal.symbol == "BTCUSDT"

    def test_none_signal_when_4h_trend_fails(self):
        """Filtre 4h invalide → NONE, quelle que soit la situation sur 1h."""
        trend_df = make_trend_df(make_trend_row(ema_fast=38000, ema_slow=40000, slope=100))
        exec_df = make_exec_df()  # 1h OK

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.NONE
        assert "4h trend" in signal.reason

    def test_none_signal_when_1h_trend_fails(self):
        """Filtre 4h OK mais 1h KO → NONE."""
        trend_df = make_trend_df()  # 4h OK
        # Close sous EMA50
        bad_exec_row = make_exec_row(close=38000, ema_fast=39500, ema_slow=38000)
        exec_df = make_exec_df(last=bad_exec_row)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.NONE
        assert "1h trend" in signal.reason

    def test_none_signal_when_quality_fails(self):
        """Filtres OK mais qualité KO → NONE."""
        trend_df = make_trend_df()
        bad_exec_row = make_exec_row(atr=200, atr_ma=400)  # ATR < ATR_MA
        exec_df = make_exec_df(last=bad_exec_row)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.NONE
        assert "quality" in signal.reason

    def test_breakout_signal_when_all_conditions_met(self):
        """Tous les filtres OK + close > rolling_high → BUY_BREAKOUT."""
        trend_df = make_trend_df()
        # close=40500 > rolling_high=40000 → breakout
        last_exec = make_exec_row(close=40500.0, rolling_high=40000.0)
        exec_df = make_exec_df(last=last_exec)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.BUY_BREAKOUT
        assert signal.setup_type == SetupType.BREAKOUT
        assert signal.symbol == "BTCUSDT"
        assert signal.close_price == pytest.approx(40500.0)
        assert signal.atr == pytest.approx(BASE_ATR)

    def test_pullback_signal_when_breakout_fails(self):
        """Pas de breakout, mais pullback valide → BUY_PULLBACK."""
        trend_df = make_trend_df()
        atr = 500.0
        close = 39850.0
        # Pas de breakout : close < rolling_high
        last_exec = make_exec_row(
            close=close,
            rolling_high=41000.0,  # close < rolling_high → pas de breakout
            ema_pb=39800.0,         # dist = 50 ≤ 250 → near EMA20
            ema_fast=39200.0,
            atr=atr,
        )
        # prev: high=39800 < close=39850 → condition close > prev_high OK
        prev_exec = make_exec_row(close=39700.0, high=39800.0)
        exec_df = make_exec_df(last=last_exec, prev=prev_exec)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.BUY_PULLBACK
        assert signal.setup_type in (SetupType.PULLBACK_EMA20, SetupType.PULLBACK_EMA50)

    def test_breakout_takes_priority_over_pullback(self):
        """Si breakout ET pullback sont tous deux valides, breakout l'emporte."""
        trend_df = make_trend_df()
        atr = 500.0
        close = 39850.0
        # Breakout ET proche EMA20 simultanément
        last_exec = make_exec_row(
            close=close,
            rolling_high=39800.0,  # close > rolling_high → breakout ✓
            ema_pb=39820.0,         # dist = 30 ≤ 250 → near EMA20 ✓
            atr=atr,
        )
        prev_exec = make_exec_row(high=39790.0)  # close > prev_high ✓
        exec_df = make_exec_df(last=last_exec, prev=prev_exec)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.BUY_BREAKOUT  # priorité breakout

    def test_none_signal_when_no_setup_matches(self):
        """Tous les filtres OK mais aucun setup → NONE."""
        trend_df = make_trend_df()
        last_exec = make_exec_row(
            close=39400.0,
            rolling_high=40000.0,  # pas de breakout
            ema_pb=37000.0,         # EMA20 trop loin
            ema_fast=37500.0,       # EMA50 trop loin
        )
        prev_exec = make_exec_row(high=40000.0)  # close < prev_high → pas de pullback
        exec_df = make_exec_df(last=last_exec, prev=prev_exec)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.signal_type == SignalType.NONE

    def test_signal_contains_required_metadata(self):
        """Un signal d'entrée doit exposer les métadonnées pour le RiskManager."""
        trend_df = make_trend_df()
        last_exec = make_exec_row(close=40500.0, rolling_high=40000.0, atr=BASE_ATR)
        exec_df = make_exec_df(last=last_exec)

        strategy = MockStrategy(trend_df=trend_df, exec_df=exec_df)
        signal = strategy.generate_signal(make_context())

        assert signal.close_price > 0, "close_price must be positive"
        assert signal.atr > 0, "atr must be positive (needed for stop-loss calc)"
        assert signal.setup_type is not None
        assert signal.reason != ""
        assert signal.timestamp is not None

    def test_no_lookahead_signal_type_is_none_returns_signal_not_none_object(self):
        """generate_signal ne doit jamais retourner None (objet Python)."""
        strategy = TrendFollowingV1()
        # Données insuffisantes → doit retourner Signal NONE, pas None
        context = StrategyContext(
            symbol="BTCUSDT",
            exec_candles=[],
            trend_candles=[],
        )
        result = strategy.generate_signal(context)
        assert result is not None
        assert isinstance(result, Signal)
        assert result.signal_type == SignalType.NONE


# ---------------------------------------------------------------------------
# Tests min_candles_required
# ---------------------------------------------------------------------------


class TestMinCandlesRequired:
    def test_returns_dict_with_both_timeframes(self):
        strategy = TrendFollowingV1()
        required = strategy.min_candles_required()
        assert "4h" in required
        assert "1h" in required

    def test_minimum_is_above_ema_slow(self):
        strategy = TrendFollowingV1()
        required = strategy.min_candles_required()
        assert required["4h"] > EMA_SLOW
        assert required["1h"] > EMA_SLOW

    def test_is_ready_false_below_threshold(self):
        strategy = TrendFollowingV1()
        required = strategy.min_candles_required()
        context = StrategyContext(
            symbol="BTCUSDT",
            exec_candles=_make_candles(required["1h"] - 1, "1h"),
            trend_candles=_make_candles(required["4h"], "4h"),
        )
        assert strategy.is_ready(context) is False

    def test_is_ready_true_at_threshold(self):
        strategy = TrendFollowingV1()
        required = strategy.min_candles_required()
        context = StrategyContext(
            symbol="BTCUSDT",
            exec_candles=_make_candles(required["1h"], "1h"),
            trend_candles=_make_candles(required["4h"], "4h"),
        )
        assert strategy.is_ready(context) is True

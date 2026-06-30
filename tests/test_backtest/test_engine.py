"""
Tests du BacktestEngine — gestion des positions.

Couvre :
- Entrée à l'open de la prochaine bougie (pas à la clôture du signal)
- Stop-loss classique
- Prise partielle à 1R + break-even
- Convention conservative intrabar (stop gagne sur 1R si les deux sont touchés)
- Trailing stop après prise partielle (avec enough data pour ATR)
- Frais appliqués correctement
- Une seule position par actif (pas de pyramiding)

Note sur le design des tests :
  BacktestDataProvider.advance() retourne False pour le DERNIER candle (considéré
  "potentiellement ouvert" en live). Pour que N bougies soient traitées, il faut
  N+1 bougies — le dernier servant de "dummy" non-traité.

  Avec N bougies [c0, c1, ..., c_{N-1}] :
    - advance() traite c0, c1, ..., c_{N-2}  (N-1 bougies)
    - c_{N-1} n'est jamais traité
  Donc pour tester un scénario sur K bougies : fournir K+1 candles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtest.engine import BacktestEngine
from config.settings import load_trading_config
from core.enums import OrderSide, SetupType, SignalType
from core.models import Candle, Signal
from risk.manager import RiskManager
from strategy.base import StrategyBase, StrategyContext

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

UTC = timezone.utc
T0 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
DT_1H = timedelta(hours=1)
DT_4H = timedelta(hours=4)

SYMBOL = "BTCUSDT"
SIGNAL_CLOSE = 50_000.0
ATR = 1_000.0
# stop_loss = close - 1.5 × ATR
STOP_LOSS = SIGNAL_CLOSE - 1.5 * ATR   # 48_500
# take_profit_1r = close + 1.5 × ATR (1R = distance au stop = 1500)
TARGET_1R = SIGNAL_CLOSE + 1.5 * ATR   # 51_500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle_1h(
    open_: float,
    high: float,
    low: float,
    close: float,
    idx: int,
    volume: float = 1_000.0,
) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe="1h",
        timestamp=T0 + idx * DT_1H,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _candle_4h(idx: int = 0) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe="4h",
        timestamp=T0 + idx * DT_4H,
        open=49_000.0,
        high=52_000.0,
        low=48_000.0,
        close=50_000.0,
        volume=5_000.0,
    )


def _warmup_candles_1h(n: int, start_idx: int = 0, price: float = 50_000.0) -> list[Candle]:
    """
    Crée N bougies 1h neutres avec des OHLCV réguliers.

    Utilisé pour "chauffer" les indicateurs (ATR, EMA) avant le vrai scénario.
    """
    return [
        _candle_1h(
            open_=price,
            high=price * 1.005,
            low=price * 0.995,
            close=price,
            idx=start_idx + i,
        )
        for i in range(n)
    ]


class _SignalOnNthCallStrategy(StrategyBase):
    """
    Stratégie de test : retourne BUY_BREAKOUT au N-ième appel, NONE sinon.

    Permet de déclencher le signal à un candle précis indépendamment des indicateurs.
    """

    def __init__(
        self,
        trigger_at: int,
        close_price: float = SIGNAL_CLOSE,
        atr: float = ATR,
    ):
        self._trigger_at = trigger_at
        self._close_price = close_price
        self._atr = atr
        self._call_count = 0

    @property
    def name(self) -> str:
        return "TestSignal"

    def generate_signal(self, context: StrategyContext) -> Signal:
        self._call_count += 1
        if self._call_count == self._trigger_at:
            return Signal(
                signal_type=SignalType.BUY_BREAKOUT,
                symbol=context.symbol,
                timeframe="1h",
                timestamp=T0,
                close_price=self._close_price,
                atr=self._atr,
                setup_type=SetupType.BREAKOUT,
                reason="test breakout",
            )
        return Signal(SignalType.NONE, context.symbol, "1h", T0)

    def min_candles_required(self) -> dict[str, int]:
        return {"1h": 1, "4h": 1}


class _NeverSignalStrategy(StrategyBase):
    @property
    def name(self):
        return "never"

    def generate_signal(self, ctx: StrategyContext) -> Signal:
        return Signal(SignalType.NONE, ctx.symbol, "1h", T0)

    def min_candles_required(self):
        return {"1h": 1, "4h": 1}


@pytest.fixture
def risk_manager() -> RiskManager:
    return RiskManager()


def _run(
    exec_candles: list[Candle],
    strategy: StrategyBase,
    risk_manager: RiskManager,
    n_4h: int = 5,
):
    """Lance le backtest avec des données synthétiques."""
    trend_candles = [_candle_4h(i) for i in range(n_4h)]
    engine = BacktestEngine(strategy=strategy, risk_manager=risk_manager)
    return engine.run(SYMBOL, trend_candles, exec_candles)


# ---------------------------------------------------------------------------
# Tests basiques
# ---------------------------------------------------------------------------


def test_no_signal_no_trade(risk_manager):
    """Sans signal, aucun trade ne doit être généré et l'equity reste constante."""
    candles = _warmup_candles_1h(6)
    result = _run(candles, _NeverSignalStrategy(), risk_manager)

    assert len(result.trades) == 0
    assert result.final_equity == result.initial_capital


def test_equity_curve_length(risk_manager):
    """
    Avec N bougies, N-1 sont traitées → equity_curve a 1 (initial) + (N-1) = N entrées.
    """
    n = 8
    candles = _warmup_candles_1h(n)
    result = _run(candles, _NeverSignalStrategy(), risk_manager)

    # Equity curve : 1 valeur initiale + 1 par itération = N entrées pour N bougies
    assert len(result.equity_curve) == n


# ---------------------------------------------------------------------------
# Entrée : timing
# ---------------------------------------------------------------------------


def test_entry_at_next_candle_open(risk_manager):
    """
    Signal à la clôture de la bougie N → entrée à l'OPEN de la bougie N+1
    avec slippage adverse (achat légèrement plus cher que l'open).
    """
    # Bougies: signal(0) | entry(1) | stop(2) | dummy(3)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    open_entry = 50_200.0
    c_entry = _candle_1h(open_entry, 50_300, 50_100, 50_250, idx=1)
    c_stop = _candle_1h(50_250, 50_300, 47_000, 47_500, idx=2)  # stop hit
    c_dummy = _candle_1h(47_500, 48_000, 46_000, 47_000, idx=3)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_entry, c_stop, c_dummy], strategy, risk_manager)

    assert len(result.trades) == 1
    trade = result.trades[0]

    cfg = load_trading_config()
    expected_fill = open_entry * (1 + cfg.backtest.slippage_pct / 100)
    assert abs(trade.entry_price - expected_fill) < 1.0, (
        f"Expected entry ≈ {expected_fill:.2f}, got {trade.entry_price:.2f}"
    )


# ---------------------------------------------------------------------------
# Stop-loss
# ---------------------------------------------------------------------------


def test_stop_loss_exit(risk_manager):
    """
    Quand low ≤ stop_loss, la position est fermée au niveau du stop (sans slippage).
    """
    # signal(0) | entry+stop_hit(1) | dummy(2)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_stop = _candle_1h(50_100, 50_200, 47_000, 47_500, idx=1)  # low < 48500
    c_dummy = _candle_1h(47_500, 48_000, 46_000, 47_000, idx=2)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_stop, c_dummy], strategy, risk_manager)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(STOP_LOSS, abs=1.0), (
        f"Exit devrait être au stop {STOP_LOSS}, got {trade.exit_price}"
    )
    assert trade.realized_pnl < 0


def test_stop_loss_not_triggered_when_low_above_stop(risk_manager):
    """
    Tant que low > stop_loss, la position reste ouverte (aucun trade fermé).
    """
    # signal(0) | entry+neutral(1) | dummy(2)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_neutral = _candle_1h(50_100, 50_300, 49_000, 50_200, idx=1)  # low=49000 > 48500
    c_dummy = _candle_1h(50_200, 50_500, 49_500, 50_300, idx=2)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_neutral, c_dummy], strategy, risk_manager)

    assert len(result.trades) == 0  # position toujours ouverte


def test_equity_decreases_after_stop_loss(risk_manager):
    """Après un stop-loss, l'equity finale est inférieure à l'initiale."""
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_stop = _candle_1h(50_100, 50_200, 47_000, 47_500, idx=1)
    c_dummy = _candle_1h(47_500, 48_000, 46_000, 47_000, idx=2)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_stop, c_dummy], strategy, risk_manager)

    assert result.final_equity < result.initial_capital


def test_fees_reduce_pnl(risk_manager):
    """Les commissions (entry + exit) sont présentes et non nulles."""
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_stop = _candle_1h(50_100, 50_200, 47_000, 47_500, idx=1)
    c_dummy = _candle_1h(47_500, 48_000, 46_000, 47_000, idx=2)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_stop, c_dummy], strategy, risk_manager)

    assert len(result.trades) == 1
    assert result.trades[0].commission_total > 0


# ---------------------------------------------------------------------------
# Conservative intrabar convention
# ---------------------------------------------------------------------------


def test_conservative_intrabar_stop_priority(risk_manager):
    """
    Si stop ET 1R touchés dans la même bougie AVANT prise partielle → stop gagne.
    Convention conservative : on prend la perte plutôt que supposer qu'on a pris le profit.
    """
    # signal(0) | entry+neutral(1) | both_hit(2) | dummy(3)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_entry = _candle_1h(50_100, 50_200, 50_050, 50_150, idx=1)   # entrée propre
    c_both = _candle_1h(50_150, 52_000, 47_000, 49_000, idx=2)    # high≥51500 ET low≤48500
    c_dummy = _candle_1h(49_000, 49_500, 48_000, 48_500, idx=3)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_entry, c_both, c_dummy], strategy, risk_manager)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Sortie au stop, pas au target 1R
    assert trade.exit_price == pytest.approx(STOP_LOSS, abs=1.0), (
        f"Stop priority expected: exit at {STOP_LOSS}, got {trade.exit_price}"
    )
    assert trade.realized_pnl < 0


# ---------------------------------------------------------------------------
# Prise partielle à 1R + break-even
# ---------------------------------------------------------------------------


def test_partial_take_at_1r(risk_manager):
    """
    Quand high ≥ take_profit_1r :
    - 50% de la position est fermée au niveau 1R
    - La position reste ouverte avec la moitié restante
    """
    # signal(0) | entry+neutral(1) | 1R_hit(2) | dummy(3)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_entry = _candle_1h(50_100, 50_200, 50_050, 50_150, idx=1)
    c_1r = _candle_1h(50_150, 52_000, 50_100, 51_800, idx=2)  # high=52000 > 51500
    c_dummy = _candle_1h(51_800, 52_000, 51_000, 51_500, idx=3)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_entry, c_1r, c_dummy], strategy, risk_manager)

    # Position partiellement fermée (toujours ouverte à la fin), ou fermée par break-even
    # La valeur de l'equity doit avoir augmenté (prise de bénéfice partielle)
    assert result.final_equity > result.initial_capital


def test_break_even_after_partial(risk_manager):
    """
    Après la prise partielle à 1R, si le prix retombe au prix d'entrée
    (break-even), la position restante est fermée sans perte.
    """
    # signal(0) | entry+neutral(1) | 1R_hit(2) | BE_hit(3) | dummy(4)
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=0)
    c_entry = _candle_1h(50_100, 50_200, 50_050, 50_150, idx=1)
    # 1R atteint, close = 51800
    c_1r = _candle_1h(50_150, 52_000, 50_100, 51_800, idx=2)
    # Prix retombe en-dessous du break-even (fill price ≈ 50125)
    # Le stop break-even = entry_price = ~50125
    # On met low=49000 pour être bien en-dessous
    c_be = _candle_1h(51_800, 52_000, 49_000, 49_500, idx=3)
    c_dummy = _candle_1h(49_500, 50_000, 48_500, 49_000, idx=4)

    strategy = _SignalOnNthCallStrategy(trigger_at=1)
    result = _run([c_signal, c_entry, c_1r, c_be, c_dummy], strategy, risk_manager)

    # Trade doit être fermé (break-even stop touché)
    assert len(result.trades) == 1
    trade = result.trades[0]
    # PnL total doit être positif grâce à la prise partielle à 1R
    # (partial gain > remainder loss at break-even)
    assert trade.realized_pnl > 0


# ---------------------------------------------------------------------------
# Trailing stop (nécessite assez de bougies pour ATR valide)
# ---------------------------------------------------------------------------


def test_trailing_stop_exit(risk_manager):
    """
    Après la prise partielle :
    - Le trailing stop est initialisé et monte avec le prix (ratchet)
    - Quand low ≤ trailing_stop, le reste est fermé

    Ce test utilise 20 bougies de warmup pour garantir un ATR valide (14 périodes).
    """
    # 20 bougies neutres pour initialiser l'ATR, puis signal, entry, 1R, trailing, dummy
    WARMUP = 20
    warmup = _warmup_candles_1h(WARMUP, start_idx=0)

    idx = WARMUP
    # Bougie signal : signal déclenché
    c_signal = _candle_1h(49_900, 50_200, 49_800, SIGNAL_CLOSE, idx=idx)
    # Bougie entry : entrée propre
    c_entry = _candle_1h(50_100, 50_200, 50_050, 50_150, idx=idx + 1)
    # Bougie 1R : high ≥ 51500, close = 51800
    #   → trailing init ≈ 51800 - 1.5 * ATR_current
    c_1r = _candle_1h(50_150, 52_000, 50_100, 51_800, idx=idx + 2)
    # Bougie trend : close = 53000
    #   → trailing ratchet ≈ 53000 - 1.5 * ATR_current (monte)
    c_trend = _candle_1h(51_800, 54_000, 51_600, 53_000, idx=idx + 3)
    # Bougie trailing : low plonge sous le trailing estimé
    #   Trailing ≈ 53000 - 1.5 * ~ATR ≈ 53000 - 1500 = 51500
    #   On met low=50000 pour être bien en-dessous de 51500
    c_trailing = _candle_1h(53_000, 53_500, 50_000, 50_500, idx=idx + 4)
    c_dummy = _candle_1h(50_500, 51_000, 49_500, 50_000, idx=idx + 5)

    all_candles = warmup + [c_signal, c_entry, c_1r, c_trend, c_trailing, c_dummy]

    # trigger_at = WARMUP+1 : le signal se déclenche au (WARMUP+1)ème appel
    # car les WARMUP bougies génèrent un signal NONE chacune
    strategy = _SignalOnNthCallStrategy(trigger_at=WARMUP + 1)
    result = _run(all_candles, strategy, risk_manager)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Le trade doit être gagnant : partial à 1R + trailing > stop original
    assert trade.exit_price is not None
    assert trade.exit_price > STOP_LOSS  # bien au-dessus du stop initial
    # Le PnL total (partial + trailing exit) doit être positif
    assert trade.realized_pnl > 0


# ---------------------------------------------------------------------------
# Pas de pyramiding
# ---------------------------------------------------------------------------


def test_no_second_entry_while_position_open(risk_manager):
    """
    Une deuxième entrée ne peut pas se faire tant qu'une position est ouverte.
    Même si la stratégie génère un signal à chaque bougie.
    """

    class _AlwaysBuyStrategy(StrategyBase):
        @property
        def name(self):
            return "always_buy"

        def generate_signal(self, ctx: StrategyContext) -> Signal:
            return Signal(
                signal_type=SignalType.BUY_BREAKOUT,
                symbol=ctx.symbol,
                timeframe="1h",
                timestamp=T0,
                close_price=SIGNAL_CLOSE,
                atr=ATR,
                setup_type=SetupType.BREAKOUT,
            )

        def min_candles_required(self):
            return {"1h": 1, "4h": 1}

    candles = _warmup_candles_1h(15)
    result = _run(candles, _AlwaysBuyStrategy(), risk_manager)

    # Au plus 1 seul trade ouvert simultanément → 0 ou 1 trade fermé
    assert len(result.trades) <= 1

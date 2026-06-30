"""
Stratégie V1 : Trend Following — implémentation complète.

Logique complète :
  1. Filtre tendance 4h : EMA50 > EMA200, pente EMA50 positive
  2. Filtre tendance 1h : EMA50 > EMA200, close > EMA50
  3. Filtres qualité 1h : ATR > ATR_MA, volume > volume_MA
  4. Setup breakout : close > plus haut des 20 bougies précédentes
  5. Setup pullback : close proche EMA20/50 ET close > high de la bougie précédente

La stratégie ne connaît ni l'exchange, ni les balances, ni le risque.
Elle reçoit uniquement des données de marché (Candle[]) et retourne un Signal.

--- Convention données ---
`iloc[-1]` = dernière bougie FERMÉE.
Contrat : tout DataProvider doit retourner uniquement des bougies fermées.
En live, le BinanceFetcher doit exclure la bougie en formation avant de passer
les données à la stratégie (la bougie courante Binance est incluse dans l'API).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from config.settings import load_trading_config
from core.enums import SetupType, SignalType
from core.exceptions import StrategyError
from core.models import Signal
from strategy.base import StrategyBase, StrategyContext
from strategy.indicators import prepare_execution_indicators, prepare_trend_indicators

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid(value: Any) -> bool:
    """
    Retourne True si la valeur est un nombre fini utilisable dans une comparaison.

    Protège contre les NaN produits par les indicateurs en période de chauffe,
    et contre les None/non-numériques.
    """
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _val(row: pd.Series, col: str) -> float | None:
    """
    Extrait une valeur d'une ligne de DataFrame.

    Retourne None si la colonne est absente ou la valeur invalide (NaN/inf).
    """
    v = row.get(col)
    return float(v) if _is_valid(v) else None


# ---------------------------------------------------------------------------
# Stratégie principale
# ---------------------------------------------------------------------------


class TrendFollowingV1(StrategyBase):
    """
    Stratégie trend-following V1 — règles exactes :

    Filtres (séquentiels, court-circuit si l'un échoue) :
      1. Tendance 4h  : EMA50 > EMA200  ET  pente EMA50 > 0
      2. Tendance 1h  : EMA50 > EMA200  ET  close > EMA50
      3. Qualité  1h  : ATR14 > ATR_MA20  ET  volume > volume_MA20

    Setups (testés dans l'ordre, le premier valide l'emporte) :
      A. Breakout : close > max(high des 20 bougies précédentes)
      B. Pullback : |close - EMA20| ≤ proximity_atr×ATR  OU
                    |close - EMA50| ≤ proximity_atr×ATR
                    ET  close > high de la bougie précédente

    Le Signal retourné contient :
      - signal_type    : BUY_BREAKOUT | BUY_PULLBACK | NONE
      - setup_type     : BREAKOUT | PULLBACK_EMA20 | PULLBACK_EMA50
      - close_price    : prix d'entrée de référence (close de la bougie signal)
      - atr            : ATR courant — le RiskManager calcule stop = close - 1.5×ATR
      - reason         : description lisible pour les logs et notifications

    Paramètres par défaut (depuis trading_config.yaml) :
      ema_fast              = 50
      ema_slow              = 200
      ema_pullback          = 20
      atr_period            = 14
      atr_ma_period         = 20
      volume_ma_period      = 20
      breakout_lookback     = 20
      pullback_proximity_atr = 0.5
    """

    def __init__(self) -> None:
        cfg = load_trading_config()
        self._cfg = cfg.strategy
        self._log = log.bind(strategy=self.name)

        # Noms des colonnes d'indicateurs (pré-calculés une fois à l'init)
        self._col = _IndicatorColumns(
            ema_fast=self._cfg.ema_fast,
            ema_slow=self._cfg.ema_slow,
            ema_pullback=self._cfg.ema_pullback,
            atr_period=self._cfg.atr_period,
            atr_ma_period=self._cfg.atr_ma_period,
            volume_ma_period=self._cfg.volume_ma_period,
            breakout_lookback=self._cfg.breakout_lookback,
        )

    @property
    def name(self) -> str:
        return "TrendFollowingV1"

    def min_candles_required(self) -> dict[str, int]:
        """
        Nombre minimum de bougies pour que les indicateurs soient valides.

        EMA200 nécessite 200 bougies pour converger + 20 de marge de sécurité.
        Le breakout lookback ajoute 20 bougies supplémentaires requises.
        On prend le max des deux besoins.
        """
        min_for_ema = self._cfg.ema_slow + 20
        min_for_exec = max(min_for_ema, self._cfg.breakout_lookback + self._cfg.atr_ma_period + 5)
        return {
            "4h": min_for_ema,     # 220 bougies 4h
            "1h": min_for_exec,    # 220+ bougies 1h
        }

    # -----------------------------------------------------------------------
    # Point d'entrée principal
    # -----------------------------------------------------------------------

    def generate_signal(self, context: StrategyContext) -> Signal:
        """
        Génère un signal à partir des données de marché.

        Flow :
          1. Calculer les indicateurs sur les deux timeframes
          2. Extraire les valeurs de la dernière bougie fermée
          3. Appliquer les filtres (court-circuit si l'un échoue)
          4. Chercher un setup
          5. Retourner le signal approprié

        Garanties :
          - Retourne toujours un Signal (jamais None)
          - Retourne NONE si les données sont insuffisantes
          - Ne lève jamais d'exception pour une donnée manquante ou NaN
        """
        if not self.is_ready(context):
            return self._no_signal(context.symbol, "Not enough candles")

        # --- Calcul des indicateurs ---
        try:
            trend_df = prepare_trend_indicators(
                context.trend_df,
                ema_fast=self._cfg.ema_fast,
                ema_slow=self._cfg.ema_slow,
            )
            exec_df = prepare_execution_indicators(
                context.exec_df,
                ema_fast=self._cfg.ema_fast,
                ema_slow=self._cfg.ema_slow,
                ema_pullback=self._cfg.ema_pullback,
                atr_period=self._cfg.atr_period,
                atr_ma_period=self._cfg.atr_ma_period,
                volume_ma_period=self._cfg.volume_ma_period,
                breakout_lookback=self._cfg.breakout_lookback,
            )
        except Exception as e:
            raise StrategyError(
                f"Indicator calculation failed for {context.symbol}: {e}"
            ) from e

        if len(trend_df) < 1 or len(exec_df) < 2:
            return self._no_signal(context.symbol, "Not enough rows after indicator computation")

        # Dernière bougie fermée (convention : iloc[-1] = dernière fermée)
        last_4h = trend_df.iloc[-1]
        last_1h = exec_df.iloc[-1]
        prev_1h = exec_df.iloc[-2]  # Bougie précédente (pour pullback)

        # --- Filtres séquentiels ---
        ok_4h, reason_4h = self._check_trend_4h(last_4h)
        if not ok_4h:
            return self._no_signal(context.symbol, f"4h trend: {reason_4h}")

        ok_1h, reason_1h = self._check_trend_1h(last_1h)
        if not ok_1h:
            return self._no_signal(context.symbol, f"1h trend: {reason_1h}")

        ok_q, reason_q = self._check_quality(last_1h)
        if not ok_q:
            return self._no_signal(context.symbol, f"quality: {reason_q}")

        # ATR courant — nécessaire pour les deux setups (métadonnée du signal)
        atr = _val(last_1h, self._col.atr)
        if atr is None:
            return self._no_signal(context.symbol, "ATR is NaN")

        # --- Setups (priorité : breakout > pullback) ---
        is_breakout, breakout_reason = self._check_breakout(last_1h)
        if is_breakout:
            return self._make_signal(
                signal_type=SignalType.BUY_BREAKOUT,
                setup_type=SetupType.BREAKOUT,
                symbol=context.symbol,
                close=float(last_1h["close"]),
                atr=atr,
                reason=breakout_reason,
                candle_ts=last_1h.name,
            )

        is_pullback, pullback_reason, pullback_setup = self._check_pullback(last_1h, prev_1h, atr)
        if is_pullback:
            return self._make_signal(
                signal_type=SignalType.BUY_PULLBACK,
                setup_type=pullback_setup,
                symbol=context.symbol,
                close=float(last_1h["close"]),
                atr=atr,
                reason=pullback_reason,
                candle_ts=last_1h.name,
            )

        return self._no_signal(context.symbol, "No valid setup")

    # -----------------------------------------------------------------------
    # Filtre tendance 4h
    # -----------------------------------------------------------------------

    def _check_trend_4h(self, last_4h: pd.Series) -> tuple[bool, str]:
        """
        Filtre de tendance sur le timeframe 4h.

        Conditions :
          - EMA50 > EMA200
          - Pente EMA50 positive (ema_50_slope > 0)
            où slope = ema[t] - ema[t-3] (3 bougies 4h = 12 heures de look-back)

        Retourne (True, "") si OK, (False, raison) sinon.
        """
        ema_fast = _val(last_4h, self._col.ema_fast_4h)
        ema_slow = _val(last_4h, self._col.ema_slow_4h)
        slope = _val(last_4h, self._col.ema_fast_slope_4h)

        if ema_fast is None or ema_slow is None:
            return False, "EMA values are NaN (insufficient data)"

        if ema_fast <= ema_slow:
            return False, f"EMA{self._cfg.ema_fast}={ema_fast:.2f} <= EMA{self._cfg.ema_slow}={ema_slow:.2f}"

        if slope is None:
            return False, "EMA slope is NaN"

        if slope <= 0:
            return False, f"EMA{self._cfg.ema_fast} slope={slope:.4f} <= 0 (not rising)"

        return True, ""

    # -----------------------------------------------------------------------
    # Filtre tendance 1h
    # -----------------------------------------------------------------------

    def _check_trend_1h(self, last_1h: pd.Series) -> tuple[bool, str]:
        """
        Confirmation de tendance sur le timeframe 1h.

        Conditions :
          - EMA50 > EMA200
          - close > EMA50

        La double confirmation (4h + 1h) réduit les faux signaux en période
        de consolidation ou de correction sur le 4h.
        """
        ema_fast = _val(last_1h, self._col.ema_fast_1h)
        ema_slow = _val(last_1h, self._col.ema_slow_1h)
        close = _val(last_1h, "close")

        if ema_fast is None or ema_slow is None:
            return False, "EMA values are NaN"

        if ema_fast <= ema_slow:
            return False, f"EMA{self._cfg.ema_fast}={ema_fast:.2f} <= EMA{self._cfg.ema_slow}={ema_slow:.2f}"

        if close is None:
            return False, "close is NaN"

        if close <= ema_fast:
            return False, f"close={close:.2f} <= EMA{self._cfg.ema_fast}={ema_fast:.2f}"

        return True, ""

    # -----------------------------------------------------------------------
    # Filtre qualité
    # -----------------------------------------------------------------------

    def _check_quality(self, last_1h: pd.Series) -> tuple[bool, str]:
        """
        Filtres de qualité marché sur le timeframe 1h.

        Conditions :
          - ATR14 > moyenne ATR sur 20 périodes
            → volatilité au-dessus de la moyenne : conditions de trending
          - volume courant > moyenne volume sur 20 périodes
            → participation du marché suffisante

        Ces deux conditions ensemble filtrent les faux breakouts en période
        de faible volatilité/liquidité.
        """
        atr = _val(last_1h, self._col.atr)
        atr_ma = _val(last_1h, self._col.atr_ma)
        volume = _val(last_1h, "volume")
        volume_ma = _val(last_1h, self._col.volume_ma)

        if atr is None or atr_ma is None:
            return False, "ATR values are NaN"

        if atr <= atr_ma:
            return False, f"ATR={atr:.4f} <= ATR_MA={atr_ma:.4f} (low volatility)"

        if volume is None or volume_ma is None:
            return False, "Volume values are NaN"

        if volume <= volume_ma:
            return False, f"volume={volume:.2f} <= volume_MA={volume_ma:.2f} (low participation)"

        return True, ""

    # -----------------------------------------------------------------------
    # Setup Breakout
    # -----------------------------------------------------------------------

    def _check_breakout(self, last_1h: pd.Series) -> tuple[bool, str]:
        """
        Setup Breakout.

        Condition :
          - close > max(high des {lookback} bougies précédentes)

        Le `rolling_high_{lookback}` est calculé avec shift(1) dans indicators.py,
        ce qui exclut la bougie courante du calcul du plus haut de référence.
        → Pas de lookahead possible.

        On exige une clôture AU-DESSUS du plus haut (strict > pour éviter les
        égalités qui ne constituent pas un vrai breakout).
        """
        close = _val(last_1h, "close")
        rolling_high = _val(last_1h, self._col.rolling_high)

        if close is None or rolling_high is None:
            return False, "close or rolling_high is NaN"

        if close > rolling_high:
            return (
                True,
                f"Breakout: close={close:.4f} > high_{self._cfg.breakout_lookback}={rolling_high:.4f}",
            )

        return False, f"close={close:.4f} <= high_{self._cfg.breakout_lookback}={rolling_high:.4f}"

    # -----------------------------------------------------------------------
    # Setup Pullback
    # -----------------------------------------------------------------------

    def _check_pullback(
        self,
        last_1h: pd.Series,
        prev_1h: pd.Series,
        atr: float,
    ) -> tuple[bool, str, SetupType | None]:
        """
        Setup Pullback de continuation.

        Conditions (toutes requises) :
          1. La bougie courante est "proche" de EMA20 ou EMA50 :
               |close - EMA| ≤ pullback_proximity_atr × ATR
             Choix V1 : on utilise le close comme prix de référence.
             Interprétation : la clôture montre que le prix a rebondi sur l'EMA.
             pullback_proximity_atr = 0.5 par défaut.

          2. close > high de la bougie précédente
             → Clôture haussière qui casse au-dessus de la résistance précédente.
             → Confirme le rebond et le retour de la pression acheteuse.

        On teste EMA20 en priorité (pullback court), puis EMA50 (pullback plus profond).
        Si les deux sont valides, on retourne PULLBACK_EMA20 (signal plus fort).

        Retourne (True, raison, SetupType) ou (False, raison, None).
        """
        close = _val(last_1h, "close")
        prev_high = _val(prev_1h, "high")
        ema_pullback = _val(last_1h, self._col.ema_pullback_1h)
        ema_fast = _val(last_1h, self._col.ema_fast_1h)
        proximity = self._cfg.pullback_proximity_atr

        # Valeurs requises
        if close is None:
            return False, "close is NaN", None
        if prev_high is None:
            return False, "prev_high is NaN", None

        # Condition 2 : close > high de la bougie précédente
        if close <= prev_high:
            return (
                False,
                f"close={close:.4f} <= prev_high={prev_high:.4f}",
                None,
            )

        # Condition 1 : proximité EMA20 ou EMA50
        near_ema20 = (
            ema_pullback is not None
            and abs(close - ema_pullback) <= proximity * atr
        )
        near_ema50 = (
            ema_fast is not None
            and abs(close - ema_fast) <= proximity * atr
        )

        if near_ema20:
            return (
                True,
                (
                    f"Pullback EMA{self._cfg.ema_pullback}: "
                    f"close={close:.4f}, ema={ema_pullback:.4f}, "
                    f"dist={abs(close - ema_pullback):.4f} ≤ {proximity}×ATR={proximity * atr:.4f}"
                ),
                SetupType.PULLBACK_EMA20,
            )

        if near_ema50:
            return (
                True,
                (
                    f"Pullback EMA{self._cfg.ema_fast}: "
                    f"close={close:.4f}, ema={ema_fast:.4f}, "
                    f"dist={abs(close - ema_fast):.4f} ≤ {proximity}×ATR={proximity * atr:.4f}"
                ),
                SetupType.PULLBACK_EMA50,
            )

        return (
            False,
            (
                f"close={close:.4f} not near EMA{self._cfg.ema_pullback} "
                f"({ema_pullback}) nor EMA{self._cfg.ema_fast} ({ema_fast}) "
                f"within {proximity}×ATR={proximity * atr:.4f}"
            ),
            None,
        )

    # -----------------------------------------------------------------------
    # Helpers de construction de signal
    # -----------------------------------------------------------------------

    def _make_signal(
        self,
        signal_type: SignalType,
        setup_type: SetupType,
        symbol: str,
        close: float,
        atr: float,
        reason: str,
        candle_ts: Any,
    ) -> Signal:
        """
        Construit un Signal d'entrée.

        Le Signal expose uniquement ce dont le RiskManager a besoin :
          - close_price → prix d'entrée de référence (ordre MARKET au prochain open)
          - atr         → le RiskManager calculera stop = close - 1.5 × ATR

        Note : le prix d'entrée réel sera légèrement différent du close
        (slippage, gap d'ouverture). C'est acceptable pour V1.
        """
        self._log.info(
            "Signal generated",
            signal=signal_type.value,
            setup=setup_type.value,
            symbol=symbol,
            close=close,
            atr=atr,
            reason=reason,
        )
        return Signal(
            signal_type=signal_type,
            symbol=symbol,
            timeframe="1h",
            timestamp=datetime.now(timezone.utc),
            close_price=close,
            atr=atr,
            setup_type=setup_type,
            reason=reason,
        )

    def _no_signal(self, symbol: str, reason: str) -> Signal:
        """Construit un Signal NONE avec la raison pour le debugging."""
        self._log.debug("No signal", symbol=symbol, reason=reason)
        return Signal(
            signal_type=SignalType.NONE,
            symbol=symbol,
            timeframe="1h",
            timestamp=datetime.now(timezone.utc),
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Helper interne : noms de colonnes
# ---------------------------------------------------------------------------


class _IndicatorColumns:
    """
    Centralise les noms des colonnes d'indicateurs.

    Évite les f-strings éparpillés dans le code et facilite la refactorisation
    si les conventions de nommage changent dans indicators.py.
    """

    def __init__(
        self,
        ema_fast: int,
        ema_slow: int,
        ema_pullback: int,
        atr_period: int,
        atr_ma_period: int,
        volume_ma_period: int,
        breakout_lookback: int,
    ) -> None:
        # Colonnes 4h (trend)
        self.ema_fast_4h = f"ema_{ema_fast}"
        self.ema_slow_4h = f"ema_{ema_slow}"
        self.ema_fast_slope_4h = f"ema_{ema_fast}_slope"

        # Colonnes 1h (exécution)
        self.ema_fast_1h = f"ema_{ema_fast}"
        self.ema_slow_1h = f"ema_{ema_slow}"
        self.ema_pullback_1h = f"ema_{ema_pullback}"
        self.atr = f"atr_{atr_period}"
        self.atr_ma = f"atr_ma_{atr_ma_period}"
        self.volume_ma = f"volume_ma_{volume_ma_period}"
        self.rolling_high = f"rolling_high_{breakout_lookback}"

"""
Interface abstraite pour une stratégie de trading.

Contrat strict :
- La stratégie reçoit uniquement des données de marché (Candle[])
- Elle retourne uniquement des Signal
- Elle ne connaît pas Binance, le portefeuille, les balances, le risque
- Elle ne place aucun ordre

C'est la separation of concerns fondamentale du bot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from core.models import Candle, Signal


@dataclass
class StrategyContext:
    """
    Contexte d'exécution d'une stratégie pour un symbole donné.

    Regroupe les données des deux timeframes nécessaires à la V1 :
    - trend_candles : bougies 4h pour le filtre de tendance
    - exec_candles  : bougies 1h pour les signaux d'entrée
    """

    symbol: str
    trend_candles: list[Candle]    # 4h
    exec_candles: list[Candle]     # 1h

    @property
    def trend_df(self) -> pd.DataFrame:
        """DataFrame 4h indexé par timestamp."""
        return _candles_to_df(self.trend_candles)

    @property
    def exec_df(self) -> pd.DataFrame:
        """DataFrame 1h indexé par timestamp."""
        return _candles_to_df(self.exec_candles)


def _candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    """Convertit une liste de Candle en DataFrame OHLCV."""
    if not candles:
        return pd.DataFrame()
    records = [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]
    df = pd.DataFrame(records).set_index("timestamp")
    df.index = pd.to_datetime(df.index, utc=True)
    return df


class StrategyBase(ABC):
    """
    Interface que toute stratégie doit implémenter.

    Une stratégie peut retourner :
    - SignalType.NONE       : pas d'action
    - SignalType.BUY_*      : signal d'entrée long
    - SignalType.SELL_*     : signal d'entrée short
    - SignalType.CLOSE_*    : signal de sortie (partielle ou totale)
    """

    @property
    def data_timeframe(self) -> str:
        """
        Timeframe des données requis par la stratégie.

        Utilisé par le replay runtime pour charger les bonnes bougies.
        Par défaut : "4h" (compatibilité avec A et B).
        Overrider à "1h" pour les stratégies intraday.
        """
        return "4h"

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom unique de la stratégie (pour logs et DB)."""
        ...

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> Signal:
        """
        Génère un signal pour un symbole donné.

        Args:
            context: Les données de marché (trend + exec timeframes)

        Returns:
            Un Signal (jamais None — utiliser SignalType.NONE)
        """
        ...

    @abstractmethod
    def min_candles_required(self) -> dict[str, int]:
        """
        Nombre minimum de bougies nécessaires par timeframe.

        Ex: {'4h': 210, '1h': 50}

        Utilisé par le runtime pour vérifier que les données sont suffisantes
        avant d'appeler generate_signal.
        """
        ...

    def is_ready(self, context: StrategyContext) -> bool:
        """
        Vérifie si les données sont suffisantes pour générer un signal.

        Par défaut, compare le nombre de bougies disponibles avec min_candles_required.
        """
        required = self.min_candles_required()
        trend_ok = len(context.trend_candles) >= required.get("4h", 0)
        exec_ok = len(context.exec_candles) >= required.get("1h", 0)
        return trend_ok and exec_ok

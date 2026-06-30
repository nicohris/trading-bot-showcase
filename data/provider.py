"""
Interface abstraite DataProvider.

La stratégie et le backtest engine consomment uniquement cette interface.
Jamais directement le fetcher Binance — découplage exchange/logique.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from core.models import Candle


class DataProvider(ABC):
    """
    Interface de fourniture de données OHLCV.

    Implémentations :
    - BinanceFetcher : données live depuis Binance REST/WS
    - BacktestDataProvider : données historiques depuis fichier/DB
    """

    @abstractmethod
    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: datetime | None = None,
    ) -> list[Candle]:
        """
        Retourne les N dernières bougies pour un symbole/timeframe.

        Args:
            symbol: Ex. 'BTCUSDT'
            timeframe: Ex. '1h', '4h'
            limit: Nombre de bougies (max 1000 pour Binance)
            since: Timestamp de début (optionnel)

        Returns:
            Liste de Candle triée par timestamp croissant.
        """
        ...

    @abstractmethod
    def get_latest_price(self, symbol: str) -> float:
        """Retourne le dernier prix disponible pour un symbole."""
        ...

    def get_candles_df(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        Version DataFrame de get_candles pour les calculs d'indicateurs.

        Le DataFrame a pour index le timestamp et les colonnes :
        open, high, low, close, volume
        """
        candles = self.get_candles(symbol, timeframe, limit)
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

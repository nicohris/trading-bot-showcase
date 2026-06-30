"""
Implémentation DataProvider pour Binance via python-binance.

Gère la conversion des données brutes Binance → Candle objects.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timezone

from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

from core.exceptions import DataError
from core.models import Candle
from data.provider import DataProvider

log = structlog.get_logger(__name__)


def _binance_kline_to_candle(raw: list, symbol: str, timeframe: str) -> Candle:
    """
    Convertit une kline brute Binance en objet Candle.

    Format Binance : [open_time, open, high, low, close, volume, ...]
    """
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(raw[0] / 1000, tz=timezone.utc),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
    )


class BinanceFetcher(DataProvider):
    """
    Fetcher de données depuis Binance REST API.

    Utilisé en live et paper trading.
    Pour le backtest, utiliser BacktestDataProvider (fichier CSV/DB).
    """

    def __init__(self, client: BinanceClient) -> None:
        self._client = client
        self._log = log.bind(component="BinanceFetcher")

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: datetime | None = None,
    ) -> list[Candle]:
        """Récupère les klines depuis l'API Binance."""
        try:
            kwargs: dict = {
                "symbol": symbol,
                "interval": timeframe,
                "limit": min(limit, 1000),  # Max Binance
            }
            if since is not None:
                kwargs["startTime"] = int(since.timestamp() * 1000)

            raw_klines = self._client.get_klines(**kwargs)
            candles = [
                _binance_kline_to_candle(k, symbol, timeframe)
                for k in raw_klines
            ]

            self._log.debug("Candles fetched", symbol=symbol, tf=timeframe, count=len(candles))
            return candles

        except BinanceAPIException as e:
            raise DataError(f"Binance API error fetching {symbol} {timeframe}: {e}") from e

    def get_latest_price(self, symbol: str) -> float:
        """Récupère le dernier prix via l'endpoint ticker."""
        try:
            ticker = self._client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except BinanceAPIException as e:
            raise DataError(f"Binance API error fetching price for {symbol}: {e}") from e


class BacktestDataProvider(DataProvider):
    """
    Provider de données pour le backtest — walk-forward bougie par bougie.

    Principe :
    - On charge toutes les données à l'avance (via HistoricalDataLoader ou load()).
    - Un curseur avance sur le timeframe d'exécution (ex: 1h).
    - get_candles() retourne les N bougies PRÉCÉDANT le curseur courant.
    - Pour les autres timeframes (ex: 4h), on filtre par timestamp courant :
      on retourne toutes les bougies dont le timestamp <= timestamp courant 1h.
      → Pas de curseur séparé à synchroniser, zéro risque de lookahead bias.

    Utilisation typique (backtest engine) :
        provider = BacktestDataProvider(exec_timeframe="1h")
        provider.load("BTCUSDT", "1h", candles_1h)
        provider.load("BTCUSDT", "4h", candles_4h)

        while provider.advance("BTCUSDT"):
            ctx = StrategyContext(
                symbol="BTCUSDT",
                exec_candles=provider.get_candles("BTCUSDT", "1h", limit=300),
                trend_candles=provider.get_candles("BTCUSDT", "4h", limit=300),
            )
            signal = strategy.generate_signal(ctx)
    """

    def __init__(self, exec_timeframe: str = "1h") -> None:
        """
        Args:
            exec_timeframe: Timeframe d'exécution (celui sur lequel le curseur avance).
        """
        self._exec_tf = exec_timeframe
        # Structure : symbol → timeframe → list[Candle] (trié chronologiquement)
        self._data: dict[str, dict[str, list[Candle]]] = {}
        # Curseur sur le timeframe d'exécution : index de la prochaine bougie à "fermer"
        self._cursor: dict[str, int] = {}

    def load(self, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        """
        Charge des données historiques pour un symbole/timeframe.

        Peut être appelé plusieurs fois pour différents timeframes du même symbole.
        Les données sont triées et dédoublonnées automatiquement.
        """
        if symbol not in self._data:
            self._data[symbol] = {}
            self._cursor[symbol] = 0  # Curseur initialisé sur le tf d'exécution

        # Tri + dédoublonnage défensif
        seen: set = set()
        unique = []
        for c in sorted(candles, key=lambda c: c.timestamp):
            if c.timestamp not in seen:
                seen.add(c.timestamp)
                unique.append(c)

        self._data[symbol][timeframe] = unique
        log.debug("Data loaded", symbol=symbol, tf=timeframe, count=len(unique))

    def advance(self, symbol: str) -> bool:
        """
        Avance le curseur d'une bougie (timeframe d'exécution).

        Retourne False quand toutes les bougies ont été consommées.
        C'est la boucle principale du backtest engine.
        """
        if symbol not in self._cursor:
            return False

        exec_candles = self._data.get(symbol, {}).get(self._exec_tf, [])
        if not exec_candles:
            return False

        self._cursor[symbol] += 1
        # On a encore des bougies si le curseur ne dépasse pas la fin
        # On laisse une bougie de marge (on ne traite pas la dernière, potentiellement ouverte)
        return self._cursor[symbol] < len(exec_candles)

    @property
    def current_timestamp(self) -> dict[str, datetime | None]:
        """Retourne le timestamp courant par symbole (pour le debugging)."""
        result = {}
        for symbol in self._cursor:
            exec_candles = self._data.get(symbol, {}).get(self._exec_tf, [])
            cursor = self._cursor[symbol]
            if 0 < cursor <= len(exec_candles):
                result[symbol] = exec_candles[cursor - 1].timestamp
            else:
                result[symbol] = None
        return result

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: datetime | None = None,
    ) -> list[Candle]:
        """
        Retourne les N bougies disponibles jusqu'au point courant dans le temps.

        Pour le timeframe d'exécution (1h) :
            Retourne les `limit` bougies précédant le curseur.
            → Les bougies [cursor:] ne sont PAS visibles (pas encore fermées).

        Pour les autres timeframes (4h) :
            Filtre par le timestamp courant du curseur 1h.
            → Retourne toutes les bougies 4h dont ts <= ts_courante_1h.
            → Pas de curseur séparé à maintenir.

        Cette approche garantit l'absence de lookahead bias.
        """
        all_candles = self._data.get(symbol, {}).get(timeframe, [])
        if not all_candles:
            return []

        if timeframe == self._exec_tf:
            # Curseur direct
            cursor = self._cursor.get(symbol, 0)
            start_idx = max(0, cursor - limit)
            return all_candles[start_idx:cursor]
        else:
            # Filtrage par timestamp courant
            current_ts = self._get_current_exec_ts(symbol)
            if current_ts is None:
                return []
            # Toutes les bougies de ce tf dont le timestamp <= timestamp courant 1h
            visible = [c for c in all_candles if c.timestamp <= current_ts]
            return visible[-limit:] if len(visible) > limit else visible

    def get_latest_price(self, symbol: str) -> float:
        """Prix de clôture de la dernière bougie du timeframe d'exécution."""
        exec_candles = self._data.get(symbol, {}).get(self._exec_tf, [])
        cursor = self._cursor.get(symbol, 0)
        if cursor > 0 and cursor <= len(exec_candles):
            return exec_candles[cursor - 1].close
        return 0.0

    def _get_current_exec_ts(self, symbol: str) -> datetime | None:
        """Timestamp de la dernière bougie d'exécution consommée."""
        exec_candles = self._data.get(symbol, {}).get(self._exec_tf, [])
        cursor = self._cursor.get(symbol, 0)
        if cursor > 0 and cursor <= len(exec_candles):
            return exec_candles[cursor - 1].timestamp
        return None

    def get_progress(self, symbol: str) -> tuple[int, int]:
        """Retourne (curseur_actuel, total_bougies) pour afficher la progression."""
        exec_candles = self._data.get(symbol, {}).get(self._exec_tf, [])
        return self._cursor.get(symbol, 0), len(exec_candles)

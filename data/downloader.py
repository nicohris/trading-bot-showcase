"""
Téléchargement paginé des données historiques Binance Spot.

Ce module gère uniquement la récupération depuis l'API.
Il ne sait rien du cache ni de la validation.

Design :
- L'API Binance retourne max 1000 klines par requête.
- python-binance expose get_historical_klines() qui pagine automatiquement.
  On l'utilise comme approche principale (simple, fiable).
- Pour les gros ranges (> 2 ans), on ajoute une barre de progression.
- Les données marché Binance Spot sont publiques : pas d'API key requis.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

from core.exceptions import DataError
from core.models import Candle

log = structlog.get_logger(__name__)

# Délai entre les requêtes paginées pour éviter les rate-limits (en secondes).
# Binance permet 1200 requêtes/minute sur l'IP — très permissif.
_REQUEST_DELAY = 0.1


def _parse_kline(raw: list, symbol: str, timeframe: str) -> Candle:
    """
    Convertit une kline brute Binance en Candle.

    Format Binance kline (12 éléments) :
    [0]  open_time (ms)
    [1]  open
    [2]  high
    [3]  low
    [4]  close
    [5]  volume
    [6]  close_time (ms)
    [7]  quote_asset_volume
    [8]  number_of_trades
    [9]  taker_buy_base_volume
    [10] taker_buy_quote_volume
    [11] ignore
    """
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(int(raw[0]) / 1000, tz=timezone.utc),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
    )


class BinanceDownloader:
    """
    Télécharge des klines historiques depuis Binance Spot.

    Utilise un client python-binance. Si aucune clé API n'est fournie,
    le client fonctionne quand même pour les endpoints publics (market data).

    Exemple d'utilisation :
        client = Client("", "")  # pas de credentials nécessaires
        downloader = BinanceDownloader(client)
        candles = downloader.download(
            symbol="BTCUSDT",
            timeframe="1h",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
    """

    # Nombre de klines par requête (maximum Binance : 1000)
    CHUNK_SIZE = 1000

    def __init__(self, client: BinanceClient) -> None:
        self._client = client
        self._log = log.bind(component="BinanceDownloader")

    def download(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        show_progress: bool = True,
    ) -> list[Candle]:
        """
        Télécharge toutes les klines entre start et end (bornes incluses).

        La pagination est gérée automatiquement.
        La bougie en cours de formation (bougie courante non fermée) est exclue.

        Args:
            symbol: Ex. 'BTCUSDT'
            timeframe: Ex. '1h', '4h' (notation Binance)
            start: Début de la période (UTC)
            end: Fin de la période (UTC)
            show_progress: Affiche le compte de candles téléchargés

        Returns:
            Liste de Candle triée par timestamp croissant, sans doublons.

        Raises:
            DataError: En cas d'erreur API Binance
        """
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware (UTC)")

        if start >= end:
            raise ValueError(f"start ({start}) must be before end ({end})")

        self._log.info(
            "Downloading historical data",
            symbol=symbol,
            timeframe=timeframe,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        all_candles: list[Candle] = []
        current_start_ms = start_ms

        try:
            while current_start_ms < end_ms:
                raw_klines = self._client.get_klines(
                    symbol=symbol,
                    interval=timeframe,
                    startTime=current_start_ms,
                    endTime=end_ms,
                    limit=self.CHUNK_SIZE,
                )

                if not raw_klines:
                    break

                chunk = [_parse_kline(k, symbol, timeframe) for k in raw_klines]
                all_candles.extend(chunk)

                # Avancer au-delà de la dernière bougie reçue
                last_open_time_ms = int(raw_klines[-1][0])
                current_start_ms = last_open_time_ms + 1

                if show_progress:
                    self._log.debug(
                        "Download progress",
                        symbol=symbol,
                        tf=timeframe,
                        total_so_far=len(all_candles),
                        last_candle=chunk[-1].timestamp.isoformat(),
                    )

                # Si on a reçu moins que CHUNK_SIZE, on a tout
                if len(raw_klines) < self.CHUNK_SIZE:
                    break

                # Petit délai pour respecter les rate limits
                time.sleep(_REQUEST_DELAY)

        except BinanceAPIException as e:
            raise DataError(
                f"Binance API error while downloading {symbol} {timeframe}: {e}"
            ) from e

        # Dédoublonnage défensif (ne devrait pas arriver, mais sécurité)
        seen: set[datetime] = set()
        unique: list[Candle] = []
        for c in all_candles:
            if c.timestamp not in seen:
                seen.add(c.timestamp)
                unique.append(c)

        self._log.info(
            "Download complete",
            symbol=symbol,
            timeframe=timeframe,
            total_candles=len(unique),
            first=unique[0].timestamp.isoformat() if unique else None,
            last=unique[-1].timestamp.isoformat() if unique else None,
        )

        return unique

    @staticmethod
    def make_public_client() -> BinanceClient:
        """
        Crée un client Binance sans authentification.

        Suffisant pour les données de marché (klines, tickers).
        Utilisé pour le téléchargement de données de backtest.
        """
        return BinanceClient("", "")

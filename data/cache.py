"""
Cache local CSV pour les données historiques.

Un fichier CSV par (symbol, timeframe).
Chemin : data/cache/{SYMBOL}_{TIMEFRAME}.csv

Stratégie de cache :
- Lecture : charge le fichier, filtre par plage de dates demandée.
- Écriture : merge les nouvelles données avec l'existant, re-sauvegarde.
- Couverture : on peut interroger quelle plage est déjà en cache pour
  déterminer ce qu'il reste à télécharger.

Format CSV :
    timestamp,open,high,low,close,volume
    2024-01-01T00:00:00+00:00,42000.0,42500.0,41800.0,42300.0,1234.5
    ...

Choix du CSV vs SQLite pour le cache :
- CSV = lisible à l'œil, importable dans Excel/pandas directement
- Pas de dépendance, zéro configuration
- Pour le volume de données backtest (< 100k candles), largement suffisant
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import structlog

from core.exceptions import DataError
from core.models import Candle

log = structlog.get_logger(__name__)

# Colonnes du CSV (ordre fixe, pas de symbole/tf dans le CSV — c'est dans le nom du fichier)
_CSV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def _candle_to_row(c: Candle) -> dict:
    return {
        "timestamp": c.timestamp.strftime(_TIMESTAMP_FORMAT),
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "volume": c.volume,
    }


def _row_to_candle(row: pd.Series, symbol: str, timeframe: str) -> Candle:
    ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


class DataCache:
    """
    Gestion du cache CSV local.

    Chaque instance pointe vers un répertoire racine.
    Les fichiers sont nommés : {SYMBOL}_{TIMEFRAME}.csv
    ex: BTCUSDT_1h.csv, ETHUSDT_4h.csv
    """

    def __init__(self, cache_dir: Path | str = "data/cache") -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log = log.bind(component="DataCache")

    def _path(self, symbol: str, timeframe: str) -> Path:
        return self._dir / f"{symbol}_{timeframe}.csv"

    def exists(self, symbol: str, timeframe: str) -> bool:
        """Retourne True si un fichier cache existe pour ce symbol/timeframe."""
        return self._path(symbol, timeframe).exists()

    def get_coverage(self, symbol: str, timeframe: str) -> tuple[datetime, datetime] | None:
        """
        Retourne (start, end) des données en cache, ou None si rien en cache.

        Utile pour déterminer ce qu'il faut télécharger.
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            return None

        try:
            # Lire seulement la première et dernière ligne pour la performance
            df = pd.read_csv(path, usecols=["timestamp"])
            if df.empty:
                return None
            timestamps = pd.to_datetime(df["timestamp"], utc=True)
            return timestamps.iloc[0].to_pydatetime(), timestamps.iloc[-1].to_pydatetime()
        except Exception as e:
            self._log.warning("Failed to read cache coverage", path=str(path), error=str(e))
            return None

    def load(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """
        Charge les candles depuis le cache, filtrées par start/end si fournis.

        Args:
            symbol: Ex. 'BTCUSDT'
            timeframe: Ex. '1h'
            start: Borne inférieure (incluse). None = depuis le début.
            end: Borne supérieure (incluse). None = jusqu'à la fin.

        Returns:
            Liste de Candle triée chronologiquement.
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            return []

        try:
            df = pd.read_csv(path)
            if df.empty:
                return []

            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)

            if start is not None:
                start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
                df = df[df["timestamp"] >= start_utc]

            if end is not None:
                end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
                df = df[df["timestamp"] <= end_utc]

            candles = [_row_to_candle(row, symbol, timeframe) for _, row in df.iterrows()]
            self._log.debug(
                "Loaded from cache",
                symbol=symbol,
                tf=timeframe,
                count=len(candles),
            )
            return candles

        except Exception as e:
            raise DataError(f"Failed to load cache for {symbol} {timeframe}: {e}") from e

    def save(self, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        """
        Sauvegarde (ou merge) des candles dans le cache.

        Si un fichier existe déjà, les nouvelles données sont mergées.
        Les doublons sont éliminés. Le résultat est trié chronologiquement.

        Args:
            symbol: Ex. 'BTCUSDT'
            timeframe: Ex. '1h'
            candles: Nouvelles candles à sauvegarder (peut chevaucher l'existant)
        """
        if not candles:
            return

        path = self._path(symbol, timeframe)

        # Charger l'existant si présent
        existing: list[Candle] = []
        if path.exists():
            existing = self.load(symbol, timeframe)

        # Merge : union par timestamp
        combined_map: dict[datetime, Candle] = {c.timestamp: c for c in existing}
        for c in candles:
            combined_map[c.timestamp] = c  # Les nouvelles données écrasent les anciennes

        merged = sorted(combined_map.values(), key=lambda c: c.timestamp)

        # Écrire le CSV
        try:
            rows = [_candle_to_row(c) for c in merged]
            df = pd.DataFrame(rows, columns=_CSV_COLUMNS)
            df.to_csv(path, index=False)

            self._log.info(
                "Cache saved",
                symbol=symbol,
                tf=timeframe,
                total_candles=len(merged),
                path=str(path),
            )
        except Exception as e:
            raise DataError(f"Failed to save cache for {symbol} {timeframe}: {e}") from e

    def delete(self, symbol: str, timeframe: str) -> bool:
        """Supprime le fichier cache. Retourne True si supprimé."""
        path = self._path(symbol, timeframe)
        if path.exists():
            path.unlink()
            self._log.info("Cache deleted", symbol=symbol, tf=timeframe)
            return True
        return False

    def list_cached(self) -> list[tuple[str, str]]:
        """Liste tous les (symbol, timeframe) présents dans le cache."""
        result = []
        for p in sorted(self._dir.glob("*.csv")):
            name = p.stem  # ex: "BTCUSDT_1h"
            # Séparer au dernier underscore pour gérer des noms comme BTCUSDT_4h
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                result.append((parts[0], parts[1]))
        return result

    def get_cache_size_mb(self, symbol: str, timeframe: str) -> float:
        """Retourne la taille du fichier cache en Mo."""
        path = self._path(symbol, timeframe)
        if not path.exists():
            return 0.0
        return path.stat().st_size / (1024 * 1024)

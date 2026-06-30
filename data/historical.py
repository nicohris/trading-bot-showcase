"""
Interface principale pour charger des données historiques.

HistoricalDataLoader orchestre :
1. Lecture du cache local (si disponible et suffisant)
2. Téléchargement des parties manquantes depuis Binance
3. Merge et sauvegarde dans le cache
4. Validation de l'intégrité
5. Retour des candles filtrées sur la plage demandée

Utilisé par :
- La commande CLI `data download`
- La commande CLI `backtest` (pour alimenter le backtest engine)
- Le BacktestDataProvider

Flux de données pour un appel load(BTCUSDT, 1h, 2024-01-01, 2024-12-31) :

    Cache?
    ├─ Non → télécharger tout → sauvegarder → valider → retourner
    └─ Oui, couvre [2024-03-01, 2025-01-01]
        ├─ Manque avant : télécharger [2024-01-01, 2024-03-01[
        ├─ Manque après : rien
        └─ Merge → sauvegarder → valider → filtrer → retourner
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from core.exceptions import DataError
from core.models import Candle
from data.cache import DataCache
from data.downloader import BinanceDownloader
from data.validator import DataValidator, ValidationResult

log = structlog.get_logger(__name__)


class HistoricalDataLoader:
    """
    Point d'entrée pour le chargement de données historiques.

    Cache-first : on télécharge uniquement ce qui manque.

    Args:
        downloader: Instance de BinanceDownloader
        cache_dir: Répertoire du cache local (défaut: data/cache)
        validate: Si True, valide les données après chargement
        stop_on_error: Si True, lève une exception si la validation échoue
    """

    def __init__(
        self,
        downloader: BinanceDownloader,
        cache_dir: Path | str = "data/cache",
        validate: bool = True,
        stop_on_error: bool = False,
    ) -> None:
        self._downloader = downloader
        self._cache = DataCache(cache_dir)
        self._validator = DataValidator()
        self._validate = validate
        self._stop_on_error = stop_on_error
        self._log = log.bind(component="HistoricalDataLoader")

    def load(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """
        Charge les candles historiques pour une plage de dates.

        Utilise le cache en priorité, télécharge uniquement les données manquantes.
        La bougie en cours de formation (si end = maintenant) est exclue automatiquement.

        Args:
            symbol: Ex. 'BTCUSDT'
            timeframe: Ex. '1h', '4h'
            start: Début de la période (UTC)
            end: Fin de la période (UTC)

        Returns:
            Liste de Candle triée chronologiquement, sans doublons.

        Raises:
            DataError: Si le téléchargement échoue ou si validation fail (stop_on_error=True)
        """
        start = _ensure_utc(start)
        end = _ensure_utc(end)

        self._log.info(
            "Loading historical data",
            symbol=symbol,
            tf=timeframe,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        # --- Vérifier ce qu'on a en cache ---
        coverage = self._cache.get_coverage(symbol, timeframe)
        missing_ranges = self._compute_missing_ranges(start, end, coverage)

        # --- Télécharger les parties manquantes ---
        if missing_ranges:
            for dl_start, dl_end in missing_ranges:
                self._log.info(
                    "Downloading missing range",
                    symbol=symbol,
                    tf=timeframe,
                    from_=dl_start.isoformat(),
                    to=dl_end.isoformat(),
                )
                new_candles = self._downloader.download(symbol, timeframe, dl_start, dl_end)
                if new_candles:
                    self._cache.save(symbol, timeframe, new_candles)
        else:
            self._log.info("Cache hit — no download needed", symbol=symbol, tf=timeframe)

        # --- Charger depuis le cache (toujours — source of truth) ---
        candles = self._cache.load(symbol, timeframe, start=start, end=end)

        if not candles:
            raise DataError(
                f"No data available for {symbol} {timeframe} "
                f"between {start.date()} and {end.date()}"
            )

        # --- Validation ---
        if self._validate:
            validation = self._validator.validate(candles, symbol, timeframe)
            self._log_validation(validation)
            if not validation.is_valid and self._stop_on_error:
                raise DataError(
                    f"Data validation failed for {symbol} {timeframe}:\n"
                    + validation.summary()
                )

        self._log.info(
            "Data ready",
            symbol=symbol,
            tf=timeframe,
            candles=len(candles),
            first=candles[0].timestamp.isoformat(),
            last=candles[-1].timestamp.isoformat(),
        )
        return candles

    def load_multi_timeframe(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframes: list[str] | None = None,
    ) -> dict[str, list[Candle]]:
        """
        Charge les données pour plusieurs timeframes en une fois.

        Utile pour le backtest engine qui a besoin de 1h et 4h.

        Args:
            symbol: Ex. 'BTCUSDT'
            start: Début de la période (UTC)
            end: Fin de la période (UTC)
            timeframes: Liste de timeframes à charger. Défaut: ['1h', '4h']

        Returns:
            Dict {timeframe: list[Candle]}
        """
        if timeframes is None:
            timeframes = ["1h", "4h"]

        result: dict[str, list[Candle]] = {}
        for tf in timeframes:
            result[tf] = self.load(symbol=symbol, timeframe=tf, start=start, end=end)

        return result

    def validate_only(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ValidationResult:
        """
        Valide les données en cache sans télécharger.

        Utile pour la commande CLI `data validate`.
        """
        candles = self._cache.load(symbol, timeframe, start=start, end=end)
        if not candles:
            raise DataError(f"No cached data found for {symbol} {timeframe}")
        return self._validator.validate(candles, symbol, timeframe)

    # -----------------------------------------------------------------------
    # Helpers privés
    # -----------------------------------------------------------------------

    def _compute_missing_ranges(
        self,
        requested_start: datetime,
        requested_end: datetime,
        coverage: tuple[datetime, datetime] | None,
    ) -> list[tuple[datetime, datetime]]:
        """
        Calcule les plages temporelles à télécharger.

        Cas possibles :
        1. Pas de cache → télécharger tout
        2. Cache couvre exactement ou plus → rien à faire
        3. Cache commence trop tard → télécharger le début
        4. Cache finit trop tôt → télécharger la fin
        5. Cache commence trop tard ET finit trop tôt → télécharger début + fin

        On ajoute une marge d'1 bougie à la jointure pour éviter les trous de jointure.
        """
        if coverage is None:
            return [(requested_start, requested_end)]

        cache_start, cache_end = coverage
        ranges: list[tuple[datetime, datetime]] = []

        # Manque au début
        if requested_start < cache_start:
            # Télécharger jusqu'à cache_start + un peu (overlap pour être sûr)
            dl_end = min(cache_start + timedelta(hours=1), requested_end)
            ranges.append((requested_start, dl_end))

        # Manque à la fin
        if requested_end > cache_end:
            # Commencer un peu avant cache_end pour l'overlap
            dl_start = max(cache_end - timedelta(hours=1), requested_start)
            ranges.append((dl_start, requested_end))

        return ranges

    def _log_validation(self, result: ValidationResult) -> None:
        if result.errors:
            self._log.error(
                "Data validation errors",
                symbol=result.symbol,
                tf=result.timeframe,
                errors=len(result.errors),
                warnings=len(result.warnings),
            )
            for issue in result.errors[:5]:
                self._log.error("Validation error", rule=issue.rule, msg=issue.message)
        elif result.warnings:
            self._log.warning(
                "Data validation warnings",
                symbol=result.symbol,
                tf=result.timeframe,
                warnings=len(result.warnings),
            )
            for issue in result.warnings[:5]:
                self._log.warning("Validation warning", rule=issue.rule, msg=issue.message)
        else:
            self._log.info(
                "Data validation passed",
                symbol=result.symbol,
                tf=result.timeframe,
                candles=result.total_candles,
            )


def _ensure_utc(dt: datetime) -> datetime:
    """S'assure qu'un datetime a une timezone UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

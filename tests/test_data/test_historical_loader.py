"""
Tests du HistoricalDataLoader — orchestrateur cache + download.

On mock le downloader pour tester la logique de cache sans réseau.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.cache import DataCache
from data.downloader import BinanceDownloader
from data.historical import HistoricalDataLoader, _ensure_utc
from tests.test_data.fixtures import make_candles


def make_loader(tmp_path, downloaded_candles=None, validate=True, stop_on_error=False):
    """
    Crée un HistoricalDataLoader avec un downloader mocké.

    downloaded_candles: ce que le downloader retournera (ou [] si None).
    """
    downloader = MagicMock(spec=BinanceDownloader)
    downloader.download.return_value = downloaded_candles or []
    return (
        HistoricalDataLoader(
            downloader=downloader,
            cache_dir=tmp_path,
            validate=validate,
            stop_on_error=stop_on_error,
        ),
        downloader,
    )


class TestHistoricalDataLoaderCacheFirst:
    def test_downloads_when_no_cache(self, tmp_path):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)
        downloaded = make_candles(count=216, start=start)  # 9 jours * 24h

        loader, downloader = make_loader(tmp_path, downloaded_candles=downloaded)
        candles = loader.load("BTCUSDT", "1h", start, end)

        downloader.download.assert_called_once()
        assert len(candles) == len(downloaded)

    def test_no_download_when_cache_covers_range(self, tmp_path):
        """Si le cache couvre toute la plage, on ne doit pas appeler le downloader."""
        start = datetime(2024, 1, 5, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, tzinfo=timezone.utc)

        # Pré-charger le cache avec des données qui couvrent toute la plage
        cache = DataCache(tmp_path)
        wide_candles = make_candles(count=240, start=datetime(2024, 1, 1, tzinfo=timezone.utc))
        cache.save("BTCUSDT", "1h", wide_candles)

        loader, downloader = make_loader(tmp_path)
        candles = loader.load("BTCUSDT", "1h", start, end)

        # Pas d'appel au downloader
        downloader.download.assert_not_called()
        assert len(candles) > 0

    def test_downloads_only_missing_start(self, tmp_path):
        """Si le cache commence trop tard, on télécharge seulement le début."""
        cache_start = datetime(2024, 1, 10, tzinfo=timezone.utc)
        request_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        request_end = datetime(2024, 1, 20, tzinfo=timezone.utc)

        # Cache commence le 10 janvier
        cache = DataCache(tmp_path)
        cached_candles = make_candles(count=241, start=cache_start)
        cache.save("BTCUSDT", "1h", cached_candles)

        # Le downloader va combler le début (1 → 10 janvier)
        missing_candles = make_candles(count=216, start=request_start)
        loader, downloader = make_loader(tmp_path, downloaded_candles=missing_candles)
        loader.load("BTCUSDT", "1h", request_start, request_end)

        # Le downloader doit avoir été appelé une fois (pour le début manquant)
        downloader.download.assert_called_once()
        call_args = downloader.download.call_args
        # Le download doit commencer au request_start
        assert call_args[0][2] == request_start or call_args[1].get("start") == request_start

    def test_saves_downloaded_data_to_cache(self, tmp_path):
        """Les données téléchargées doivent être persistées dans le cache."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 5, tzinfo=timezone.utc)
        downloaded = make_candles(count=96, start=start)

        loader, _ = make_loader(tmp_path, downloaded_candles=downloaded)
        loader.load("BTCUSDT", "1h", start, end)

        # Vérifier que le cache contient maintenant les données
        cache = DataCache(tmp_path)
        assert cache.exists("BTCUSDT", "1h")


class TestHistoricalDataLoaderErrors:
    def test_raises_when_no_data_available(self, tmp_path):
        """Si aucune donnée n'est disponible (download vide), lève DataError."""
        loader, _ = make_loader(tmp_path, downloaded_candles=[])

        from core.exceptions import DataError
        with pytest.raises(DataError):
            loader.load(
                "BTCUSDT", "1h",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 5, tzinfo=timezone.utc),
            )


class TestHistoricalDataLoaderMultiTimeframe:
    def test_load_multi_returns_dict(self, tmp_path):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)

        candles_1h = make_candles(timeframe="1h", count=216, start=start)
        candles_4h = make_candles(timeframe="4h", count=54, start=start, interval_hours=4)

        downloader = MagicMock(spec=BinanceDownloader)
        # Retourne des données différentes selon le timeframe
        downloader.download.side_effect = lambda sym, tf, s, e, **kw: (
            candles_1h if tf == "1h" else candles_4h
        )

        loader = HistoricalDataLoader(
            downloader=downloader, cache_dir=tmp_path, validate=False
        )
        result = loader.load_multi_timeframe("BTCUSDT", start, end, timeframes=["1h", "4h"])

        assert "1h" in result
        assert "4h" in result
        assert len(result["1h"]) > 0
        assert len(result["4h"]) > 0

    def test_load_multi_default_timeframes(self, tmp_path):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 5, tzinfo=timezone.utc)

        candles = make_candles(count=100, start=start)
        downloader = MagicMock(spec=BinanceDownloader)
        downloader.download.return_value = candles

        loader = HistoricalDataLoader(
            downloader=downloader, cache_dir=tmp_path, validate=False
        )
        result = loader.load_multi_timeframe("BTCUSDT", start, end)
        # Défaut : 1h et 4h
        assert set(result.keys()) == {"1h", "4h"}


class TestEnsureUtc:
    def test_naive_datetime_gets_utc(self):
        naive = datetime(2024, 1, 1)
        result = _ensure_utc(naive)
        assert result.tzinfo == timezone.utc

    def test_utc_datetime_unchanged(self):
        aware = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _ensure_utc(aware)
        assert result == aware

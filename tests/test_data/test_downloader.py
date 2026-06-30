"""
Tests du BinanceDownloader.

On mock le client Binance pour ne pas faire de vraies requêtes réseau.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.downloader import BinanceDownloader, _parse_kline
from tests.test_data.fixtures import make_raw_kline


def make_mock_client(raw_klines_pages: list[list]) -> MagicMock:
    """
    Crée un client Binance mocké qui retourne les pages spécifiées.

    raw_klines_pages : liste de pages, chaque page est une liste de klines brutes.
    Le mock retourne les pages dans l'ordre, puis [] pour signifier la fin.
    """
    client = MagicMock()
    # get_klines est appelé à chaque page
    client.get_klines.side_effect = raw_klines_pages + [[]]
    return client


class TestParseKline:
    def test_basic_parse(self):
        ts_ms = 1704067200000  # 2024-01-01 00:00:00 UTC
        raw = make_raw_kline(ts_ms, price=42000.0)
        candle = _parse_kline(raw, "BTCUSDT", "1h")

        assert candle.symbol == "BTCUSDT"
        assert candle.timeframe == "1h"
        assert candle.timestamp == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert candle.close == pytest.approx(42000.0)
        assert candle.volume == pytest.approx(1000.0)

    def test_timestamp_is_utc(self):
        ts_ms = 1704067200000
        raw = make_raw_kline(ts_ms)
        candle = _parse_kline(raw, "BTCUSDT", "1h")
        assert candle.timestamp.tzinfo == timezone.utc

    def test_ohlc_values(self):
        ts_ms = 1704067200000
        price = 50000.0
        raw = make_raw_kline(ts_ms, price=price)
        candle = _parse_kline(raw, "BTCUSDT", "1h")
        assert candle.high == pytest.approx(price * 1.002)
        assert candle.low == pytest.approx(price * 0.998)
        assert candle.open == pytest.approx(price * 0.999)


class TestBinanceDownloader:
    def _make_downloader(self, raw_klines_pages):
        client = make_mock_client(raw_klines_pages)
        return BinanceDownloader(client), client

    def test_download_single_page(self):
        """Un seul appel API suffit pour < 1000 klines."""
        start_ms = 1704067200000  # 2024-01-01
        klines = [make_raw_kline(start_ms + i * 3600000, 40000 + i) for i in range(10)]

        downloader, client = self._make_downloader([klines])

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        candles = downloader.download("BTCUSDT", "1h", start, end, show_progress=False)

        assert len(candles) == 10
        client.get_klines.assert_called()

    def test_download_removes_duplicates(self):
        """La déduplication doit fonctionner même si l'API retourne des doublons."""
        start_ms = 1704067200000
        kline = make_raw_kline(start_ms, 40000)

        # Deux pages avec le même timestamp dans la dernière/première bougie (overlap pagination)
        page1 = [make_raw_kline(start_ms + i * 3600000, 40000 + i) for i in range(5)]
        page2 = [make_raw_kline(start_ms + 4 * 3600000, 40004)]  # Doublon du dernier de page1

        downloader, _ = self._make_downloader([page1, page2])
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        candles = downloader.download("BTCUSDT", "1h", start, end, show_progress=False)

        # 5 de page1 + 1 de page2, mais le doublon doit être éliminé
        timestamps = [c.timestamp for c in candles]
        assert len(timestamps) == len(set(timestamps)), "Duplicates found"

    def test_download_sorted_output(self):
        """Les candles retournées doivent être triées chronologiquement."""
        start_ms = 1704067200000
        klines = [make_raw_kline(start_ms + i * 3600000, 40000 + i) for i in range(5)]

        downloader, _ = self._make_downloader([klines])
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        candles = downloader.download("BTCUSDT", "1h", start, end, show_progress=False)

        for i in range(1, len(candles)):
            assert candles[i].timestamp > candles[i - 1].timestamp

    def test_download_raises_on_invalid_dates(self):
        """start >= end doit lever une ValueError."""
        client = MagicMock()
        downloader = BinanceDownloader(client)
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="must be before"):
            downloader.download("BTCUSDT", "1h", start, end)

    def test_download_raises_on_naive_datetime(self):
        """Les datetimes sans timezone doivent lever une ValueError."""
        client = MagicMock()
        downloader = BinanceDownloader(client)
        with pytest.raises(ValueError, match="timezone-aware"):
            downloader.download(
                "BTCUSDT", "1h",
                datetime(2024, 1, 1),  # naive (pas de tzinfo)
                datetime(2024, 12, 31, tzinfo=timezone.utc),
            )

    def test_make_public_client_returns_client(self):
        """make_public_client doit retourner un objet Client."""
        from binance.client import Client
        client = BinanceDownloader.make_public_client()
        assert isinstance(client, Client)

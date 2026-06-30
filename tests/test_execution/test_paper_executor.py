"""
Tests du PaperExecutor.

Vérifie la simulation d'exécution : prix, slippage, commissions.
Utilise un DataProvider mocké.
"""

from unittest.mock import MagicMock

import pytest

from core.enums import OrderSide, OrderStatus, OrderType
from core.models import OrderRequest
from core.utils import generate_id
from execution.paper import PaperExecutor


def make_data_provider(price: float = 40000.0):
    """Crée un DataProvider mock retournant un prix fixe."""
    provider = MagicMock()
    provider.get_latest_price.return_value = price
    return provider


def make_order_request(
    symbol: str = "BTCUSDT",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 0.1,
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        client_order_id=generate_id("test"),
    )


class TestPaperExecutor:
    def setup_method(self):
        self.provider = make_data_provider(price=40000.0)
        self.executor = PaperExecutor(
            data_provider=self.provider,
            fee_rate=0.001,
            slippage_pct=0.05,
        )

    def test_execute_returns_filled_order(self):
        request = make_order_request(quantity=0.1)
        order = self.executor.execute(request)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 0.1

    def test_execute_applies_slippage(self):
        """Le prix de remplissage doit être légèrement au-dessus du marché."""
        request = make_order_request(quantity=0.1)
        order = self.executor.execute(request)
        market_price = 40000.0
        expected_fill = market_price * (1 + 0.05 / 100)
        assert order.avg_fill_price == pytest.approx(expected_fill)

    def test_execute_calculates_commission(self):
        request = make_order_request(quantity=0.1)
        order = self.executor.execute(request)
        expected_fill = 40000.0 * (1 + 0.05 / 100)
        expected_commission = expected_fill * 0.1 * 0.001
        assert order.commission == pytest.approx(expected_commission)

    def test_order_retrievable_by_id(self):
        request = make_order_request()
        order = self.executor.execute(request)
        retrieved = self.executor.get_order_status(order.symbol, order.id)
        assert retrieved.id == order.id

    def test_cancel_order(self):
        request = make_order_request()
        order = self.executor.execute(request)
        cancelled = self.executor.cancel(order.symbol, order.id)
        assert cancelled.status == OrderStatus.CANCELED

    def test_invalid_price_raises(self):
        """Si le DataProvider retourne 0, une erreur doit être levée."""
        bad_provider = make_data_provider(price=0.0)
        executor = PaperExecutor(data_provider=bad_provider)
        with pytest.raises(Exception):
            executor.execute(make_order_request())

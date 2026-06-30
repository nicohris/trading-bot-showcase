"""
Paper Trading Executor.

Simule l'exécution des ordres sans envoyer quoi que ce soit à l'exchange.
Utilise le prix de marché actuel avec un slippage configurable.

Idéal pour valider la stratégie sur des données live sans risque financier.
"""

from __future__ import annotations

import structlog

from core.enums import OrderStatus
from core.exceptions import ExecutionError
from core.models import Order, OrderRequest
from core.utils import generate_id, utcnow
from data.provider import DataProvider
from execution.base import ExecutorBase

log = structlog.get_logger(__name__)


class PaperExecutor(ExecutorBase):
    """
    Simule l'exécution des ordres en paper trading.

    Hypothèses de simulation :
    - Les ordres MARKET sont exécutés immédiatement au prix de marché ± slippage
    - Le slippage est simulé comme un % fixe défavorable
    - Pas de gestion des ordres partiellement remplis (tout ou rien)
    - Les commissions sont simulées selon le fee_rate configuré
    """

    def __init__(
        self,
        data_provider: DataProvider,
        fee_rate: float = 0.001,  # 0.1% Binance maker/taker
        slippage_pct: float = 0.05,  # 0.05% slippage adverse
    ) -> None:
        self._provider = data_provider
        self._fee_rate = fee_rate
        self._slippage_pct = slippage_pct
        self._orders: dict[str, Order] = {}  # Registre interne des ordres simulés
        self._log = log.bind(executor="paper")

    def execute(self, request: OrderRequest) -> Order:
        """Simule l'exécution immédiate au prix de marché avec slippage."""
        market_price = self._provider.get_latest_price(request.symbol)
        if market_price <= 0:
            raise ExecutionError(f"Invalid market price for {request.symbol}: {market_price}")

        # Slippage adverse : on paye un peu plus à l'achat
        fill_price = market_price * (1 + self._slippage_pct / 100)
        commission = fill_price * request.quantity * self._fee_rate

        order = Order(
            id=generate_id(prefix="paper"),
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.FILLED,
            quantity=request.quantity,
            filled_quantity=request.quantity,
            price=request.price,
            avg_fill_price=fill_price,
            commission=commission,
            commission_asset="USDT",
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        self._orders[order.id] = order
        self._log.info(
            "Paper order executed",
            order_id=order.id,
            symbol=request.symbol,
            side=request.side,
            qty=request.quantity,
            fill_price=fill_price,
            commission=commission,
        )
        return order

    def execute_at_price(
        self,
        symbol: str,
        side: "OrderSide",
        quantity: float,
        price: float,
        apply_slippage: bool = False,
    ) -> Order:
        """
        Crée un ordre simulé à un prix fixé.

        Utilisé par le BacktestEngine pour des entrées/sorties à un prix précis
        (open de la prochaine bougie, niveau de stop, niveau de take-profit).

        Args:
            symbol: Symbole tradé
            side: BUY ou SELL
            quantity: Quantité à exécuter
            price: Prix de référence
            apply_slippage: Si True, applique le slippage adverse (pour les entrées marché)
        """
        from core.enums import OrderSide, OrderType

        fill_price = price
        if apply_slippage:
            if side == OrderSide.BUY:
                fill_price = price * (1 + self._slippage_pct / 100)
            else:
                fill_price = price * (1 - self._slippage_pct / 100)

        commission = fill_price * quantity * self._fee_rate

        order = Order(
            id=generate_id(prefix="bt"),
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            quantity=quantity,
            filled_quantity=quantity,
            avg_fill_price=fill_price,
            commission=commission,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._orders[order.id] = order
        self._log.debug(
            "Backtest fill at price",
            symbol=symbol,
            qty=quantity,
            price=price,
            fill_price=round(fill_price, 4),
            commission=round(commission, 4),
        )
        return order

    def cancel(self, symbol: str, order_id: str) -> Order:
        """Annule un ordre papier simulé."""
        if order_id not in self._orders:
            raise ExecutionError(f"Order {order_id} not found in paper orders")
        order = self._orders[order_id]
        cancelled = order.model_copy(
            update={"status": OrderStatus.CANCELED, "updated_at": utcnow()}
        )
        self._orders[order_id] = cancelled
        return cancelled

    def get_order_status(self, symbol: str, order_id: str) -> Order:
        """Retourne le statut d'un ordre papier."""
        if order_id not in self._orders:
            raise ExecutionError(f"Order {order_id} not found in paper orders")
        return self._orders[order_id]

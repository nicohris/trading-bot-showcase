"""
Live Trading Executor.

Envoie les ordres réels à l'exchange via ExchangeInterface.
Couche mince : toute la logique est dans l'exchange et le risk manager.
"""

from __future__ import annotations

import structlog

from core.exceptions import ExecutionError
from core.models import Order, OrderRequest
from execution.base import ExecutorBase
from exchange.base import ExchangeInterface

log = structlog.get_logger(__name__)


class LiveExecutor(ExecutorBase):
    """
    Exécute les ordres sur l'exchange réel.

    Délègue entièrement à ExchangeInterface.
    Ajoute uniquement le logging et la gestion d'erreur propre.
    """

    def __init__(self, exchange: ExchangeInterface) -> None:
        self._exchange = exchange
        self._log = log.bind(executor="live")

    def execute(self, request: OrderRequest) -> Order:
        """Envoie l'ordre à l'exchange."""
        self._log.info(
            "Executing live order",
            symbol=request.symbol,
            side=request.side,
            qty=request.quantity,
            type=request.order_type,
        )
        try:
            order = self._exchange.place_order(request)
            self._log.info(
                "Live order executed",
                order_id=order.id,
                status=order.status,
                avg_price=order.avg_fill_price,
            )
            return order
        except Exception as e:
            self._log.error("Live order failed", error=str(e), symbol=request.symbol)
            raise ExecutionError(f"Live execution failed for {request.symbol}: {e}") from e

    def cancel(self, symbol: str, order_id: str) -> Order:
        return self._exchange.cancel_order(symbol, order_id)

    def get_order_status(self, symbol: str, order_id: str) -> Order:
        return self._exchange.get_order(symbol, order_id)

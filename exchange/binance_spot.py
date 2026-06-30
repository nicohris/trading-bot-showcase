"""
Implémentation ExchangeInterface pour Binance Spot.

Traduit les objets internes (OrderRequest) en appels API Binance
et les réponses Binance en objets internes (Order, AccountSnapshot).
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

from core.enums import OrderSide, OrderStatus, OrderType
from core.exceptions import ExchangeError, InsufficientFundsError
from core.models import AccountSnapshot, Order, OrderRequest, Position
from core.utils import generate_id, utcnow
from exchange.base import ExchangeInterface

log = structlog.get_logger(__name__)


def _parse_order_side(side_str: str) -> OrderSide:
    return OrderSide.BUY if side_str == "BUY" else OrderSide.SELL


def _parse_order_status(status_str: str) -> OrderStatus:
    mapping = {
        "NEW": OrderStatus.OPEN,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELED,
        "REJECTED": OrderStatus.REJECTED,
        "EXPIRED": OrderStatus.EXPIRED,
    }
    return mapping.get(status_str, OrderStatus.OPEN)


def _parse_order_type(type_str: str) -> OrderType:
    mapping = {
        "MARKET": OrderType.MARKET,
        "LIMIT": OrderType.LIMIT,
        "STOP_LOSS": OrderType.STOP_LOSS,
        "STOP_LOSS_LIMIT": OrderType.STOP_LOSS_LIMIT,
        "TAKE_PROFIT": OrderType.TAKE_PROFIT,
        "TAKE_PROFIT_LIMIT": OrderType.TAKE_PROFIT_LIMIT,
    }
    return mapping.get(type_str, OrderType.MARKET)


def _binance_order_to_order(raw: dict) -> Order:
    """Convertit la réponse API Binance en objet Order interne."""
    fills = raw.get("fills", [])
    avg_price = None
    if fills:
        total_qty = sum(float(f["qty"]) for f in fills)
        if total_qty > 0:
            avg_price = sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
    elif raw.get("price") and float(raw["price"]) > 0:
        avg_price = float(raw["price"])

    commission = sum(float(f.get("commission", 0)) for f in fills)
    commission_asset = fills[0].get("commissionAsset", "USDT") if fills else "USDT"

    return Order(
        id=str(raw["orderId"]),
        client_order_id=raw.get("clientOrderId", ""),
        symbol=raw["symbol"],
        side=_parse_order_side(raw["side"]),
        order_type=_parse_order_type(raw["type"]),
        status=_parse_order_status(raw["status"]),
        quantity=float(raw["origQty"]),
        filled_quantity=float(raw.get("executedQty", 0)),
        price=float(raw["price"]) if raw.get("price") and float(raw["price"]) > 0 else None,
        avg_fill_price=avg_price,
        commission=commission,
        commission_asset=commission_asset,
        created_at=datetime.fromtimestamp(raw["transactTime"] / 1000, tz=timezone.utc)
        if "transactTime" in raw
        else utcnow(),
        updated_at=utcnow(),
    )


class BinanceSpotExchange(ExchangeInterface):
    """
    Connecteur Binance Spot.

    En production, utiliser avec les vraies clés API.
    Pour paper trading, utiliser le testnet Binance (binance_testnet=True).

    Note : Binance Spot n'a pas de positions "officielles" (contrairement aux futures).
    Les positions sont reconstruites depuis l'historique des ordres et la balance.
    """

    def __init__(self, client: BinanceClient) -> None:
        self._client = client
        self._log = log.bind(component="BinanceSpotExchange")

    def get_account_snapshot(self) -> AccountSnapshot:
        """
        Récupère les balances et reconstruit un AccountSnapshot.

        Note Spot : l'equity totale = somme des balances converties en USDT.
        TODO: Ajouter la conversion multi-asset en USDT pour l'equity.
        """
        try:
            account = self._client.get_account()
            balances = {
                b["asset"]: float(b["free"]) + float(b["locked"])
                for b in account["balances"]
                if float(b["free"]) + float(b["locked"]) > 0
            }
            usdt_balance = balances.get("USDT", 0.0)
            usdt_free = float(
                next(
                    (b["free"] for b in account["balances"] if b["asset"] == "USDT"),
                    0.0,
                )
            )

            # TODO: Calculer les positions ouvertes depuis les balances non-USDT
            # et les ordres ouverts (stop-loss, etc.)

            return AccountSnapshot(
                timestamp=utcnow(),
                total_equity=usdt_balance,  # Simplifié : USDT seulement pour V1
                available_balance=usdt_free,
                open_positions=[],  # TODO: Reconstruire depuis les balances
            )

        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to get account snapshot: {e}", code=e.code) from e

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        try:
            kwargs = {"symbol": symbol} if symbol else {}
            raw_orders = self._client.get_open_orders(**kwargs)
            return [_binance_order_to_order(o) for o in raw_orders]
        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to get open orders: {e}", code=e.code) from e

    def get_order(self, symbol: str, order_id: str) -> Order:
        try:
            raw = self._client.get_order(symbol=symbol, orderId=int(order_id))
            return _binance_order_to_order(raw)
        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to get order {order_id}: {e}", code=e.code) from e

    def place_order(self, request: OrderRequest) -> Order:
        """Envoie l'ordre à Binance."""
        try:
            self._log.info(
                "Placing order",
                symbol=request.symbol,
                side=request.side,
                type=request.order_type,
                qty=request.quantity,
            )

            params: dict = {
                "symbol": request.symbol,
                "side": request.side.value,
                "type": request.order_type.value,
                "quantity": request.quantity,
            }

            if request.client_order_id:
                params["newClientOrderId"] = request.client_order_id

            if request.order_type == OrderType.LIMIT:
                if request.price is None:
                    raise ExchangeError("LIMIT order requires a price")
                params["price"] = str(request.price)
                params["timeInForce"] = "GTC"

            if request.order_type in (OrderType.STOP_LOSS_LIMIT, OrderType.STOP_LOSS):
                if request.stop_loss is None:
                    raise ExchangeError("STOP_LOSS order requires stop_loss price")
                params["stopPrice"] = str(request.stop_loss)

            raw = self._client.create_order(**params)
            order = _binance_order_to_order(raw)

            self._log.info(
                "Order placed",
                order_id=order.id,
                status=order.status,
                avg_price=order.avg_fill_price,
            )
            return order

        except BinanceAPIException as e:
            # Code -2010 : insufficient balance
            if e.code == -2010:
                raise InsufficientFundsError(f"Insufficient funds: {e}") from e
            raise ExchangeError(f"Failed to place order: {e}", code=e.code) from e

    def cancel_order(self, symbol: str, order_id: str) -> Order:
        try:
            raw = self._client.cancel_order(symbol=symbol, orderId=int(order_id))
            return _binance_order_to_order(raw)
        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to cancel order {order_id}: {e}", code=e.code) from e

    def cancel_all_orders(self, symbol: str) -> list[Order]:
        try:
            raw_orders = self._client.get_open_orders(symbol=symbol)
            cancelled = []
            for o in raw_orders:
                raw = self._client.cancel_order(symbol=symbol, orderId=o["orderId"])
                cancelled.append(_binance_order_to_order(raw))
            return cancelled
        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to cancel all orders for {symbol}: {e}", code=e.code) from e

    def get_symbol_info(self, symbol: str) -> dict:
        """Retourne les règles de trading Binance pour un symbole."""
        try:
            info = self._client.get_symbol_info(symbol)
            if info is None:
                raise ExchangeError(f"Symbol {symbol} not found on Binance")
            return info
        except BinanceAPIException as e:
            raise ExchangeError(f"Failed to get symbol info for {symbol}: {e}") from e

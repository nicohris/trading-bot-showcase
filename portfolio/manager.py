"""
Portfolio Manager — état courant du portefeuille.

Responsabilités :
- Maintenir la liste des positions ouvertes
- Calculer le PnL non réalisé et réalisé
- Ouvrir/mettre à jour/fermer des positions
- Notifier le RiskManager des fermetures
- Déclencher les mises à jour de trailing stop

Le PortfolioManager est la source de vérité sur l'état des positions.
"""

from __future__ import annotations

import structlog

from core.enums import OrderSide, PositionStatus
from core.models import Order, OrderRequest, Position, Trade
from core.utils import generate_id, utcnow

log = structlog.get_logger(__name__)


class PortfolioManager:
    """
    Gère l'état des positions et des trades.

    En live/paper : synchronisé avec l'exchange via le runtime.
    En backtest  : alimenté directement par le backtest engine.

    Suivi de l'équité :
    - _initial_equity : capital de départ (constant)
    - _realized_pnl   : somme des PnL nets réalisés (augmente à chaque clôture)
    - equity property : _initial_equity + _realized_pnl (cash disponible approximatif)
    """

    def __init__(self, initial_equity: float = 0.0) -> None:
        self._initial_equity = initial_equity
        self._realized_pnl: float = 0.0
        self._positions: dict[str, Position] = {}  # symbol → Position
        self._open_trades: dict[str, Trade] = {}   # trade_id → Trade
        self._closed_trades: list[Trade] = []
        self._log = log.bind(component="PortfolioManager")

    # -----------------------------------------------------------------------
    # Accesseurs
    # -----------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Capital disponible = capital initial + PnL réalisé cumulé."""
        return self._initial_equity + self._realized_pnl

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def closed_trades(self) -> list[Trade]:
        return list(self._closed_trades)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # -----------------------------------------------------------------------
    # Gestion des positions
    # -----------------------------------------------------------------------

    def open_position(
        self,
        order: Order,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        initial_stop: float | None = None,
        take_profit_1r: float | None = None,
        initial_risk: float = 0.0,
    ) -> Position:
        """
        Ouvre une position suite à l'exécution d'un ordre d'entrée.

        Crée aussi le Trade associé pour le suivi historique.

        Args:
            order: Ordre d'entrée exécuté (doit être FILLED)
            stop_loss: Niveau de stop-loss courant
            take_profit: Niveau de take-profit principal
            initial_stop: SL original du signal (référence pour calcul 1R).
                          Si None, utilise stop_loss.
            take_profit_1r: Cible 1R pour prise partielle 50%.
                            Si None, utilise take_profit.
            initial_risk: $ risqués sur ce trade (pour suivi R-multiple)
        """
        if not order.is_filled or order.avg_fill_price is None:
            raise ValueError(f"Cannot open position: order {order.id} not filled")

        trade_id = generate_id(prefix="trade")
        position = Position(
            symbol=order.symbol,
            side=order.side,
            quantity=order.filled_quantity,
            entry_price=order.avg_fill_price,
            current_price=order.avg_fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=utcnow(),
            trade_id=trade_id,
            # Gestion avancée
            initial_stop=initial_stop if initial_stop is not None else stop_loss,
            take_profit_1r=take_profit_1r if take_profit_1r is not None else take_profit,
            initial_quantity=order.filled_quantity,
            initial_risk=initial_risk,
        )
        self._positions[order.symbol] = position

        # Créer le Trade associé
        trade = Trade(
            id=trade_id,
            symbol=order.symbol,
            side=order.side,
            entry_order_id=order.id,
            entry_price=order.avg_fill_price,
            entry_quantity=order.filled_quantity,
            entry_time=utcnow(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            initial_risk=initial_risk,
            commission_total=order.commission,
        )
        trade.take_profit_1r = position.take_profit_1r
        self._open_trades[trade_id] = trade

        self._log.info(
            "Position opened",
            symbol=order.symbol,
            side=order.side,
            qty=order.filled_quantity,
            entry=order.avg_fill_price,
            stop_loss=stop_loss,
            take_profit_1r=position.take_profit_1r,
            trade_id=trade_id,
        )
        return position

    def update_position_price(self, symbol: str, current_price: float) -> None:
        """Met à jour le prix courant d'une position (pour PnL unrealized)."""
        if symbol in self._positions:
            self._positions[symbol].current_price = current_price

    def close_position(
        self,
        symbol: str,
        order: Order,
        partial: bool = False,
    ) -> Trade | None:
        """
        Ferme (totalement ou partiellement) une position.

        Met à jour _realized_pnl sur chaque clôture (partielle ou totale).

        Args:
            symbol: Symbole de la position à fermer
            order: Ordre de sortie exécuté
            partial: Si True, ne ferme qu'une partie (prise partielle)

        Returns:
            Le Trade mis à jour. None si la position n'existe pas.
        """
        position = self._positions.get(symbol)
        if position is None:
            self._log.warning("close_position called but no position found", symbol=symbol)
            return None

        if order.avg_fill_price is None:
            self._log.error("Exit order has no fill price", order_id=order.id)
            return None

        # Calcul PnL brut de cette sortie
        exit_qty = order.filled_quantity
        if position.side == OrderSide.BUY:
            pnl = (order.avg_fill_price - position.entry_price) * exit_qty
        else:
            pnl = (position.entry_price - order.avg_fill_price) * exit_qty

        pnl -= order.commission  # Déduire la commission de sortie

        # Mise à jour du PnL réalisé
        self._realized_pnl += pnl

        trade = self._open_trades.get(position.trade_id)
        if trade:
            trade.exit_orders.append(order.id)
            trade.exit_price = order.avg_fill_price
            trade.exit_quantity += exit_qty
            trade.exit_time = utcnow()
            trade.realized_pnl += pnl
            trade.commission_total += order.commission

            if partial:
                position.quantity -= exit_qty
                position.partial_taken = True
                trade.partial_taken = True
                trade.status = PositionStatus.PARTIALLY_CLOSED
                self._log.info(
                    "Position partially closed",
                    symbol=symbol,
                    qty_closed=exit_qty,
                    qty_remaining=position.quantity,
                    pnl=round(pnl, 4),
                )
            else:
                # Fermeture totale
                trade.status = PositionStatus.CLOSED
                trade.exit_reason = position.exit_reason
                self._closed_trades.append(trade)
                del self._open_trades[position.trade_id]
                del self._positions[symbol]
                self._log.info(
                    "Position closed",
                    symbol=symbol,
                    entry=position.entry_price,
                    exit=order.avg_fill_price,
                    pnl=round(pnl, 4),
                    total_realized=round(trade.realized_pnl, 4),
                    exit_reason=position.exit_reason,
                )

        return trade

    def update_stop_loss(self, symbol: str, new_stop: float) -> None:
        """Met à jour le stop-loss d'une position ouverte (trailing)."""
        if symbol in self._positions:
            self._positions[symbol].stop_loss = new_stop
            self._log.debug("Stop updated", symbol=symbol, new_stop=new_stop)

    def set_break_even(self, symbol: str) -> None:
        """Déplace le stop-loss au prix d'entrée (break-even)."""
        position = self._positions.get(symbol)
        if position and not position.break_even_set:
            position.stop_loss = position.entry_price
            position.break_even_set = True
            self._log.info("Break-even set", symbol=symbol, level=position.entry_price)

    # -----------------------------------------------------------------------
    # Métriques
    # -----------------------------------------------------------------------

    @property
    def total_realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def win_rate(self) -> float:
        if not self._closed_trades:
            return 0.0
        winners = sum(1 for t in self._closed_trades if t.realized_pnl > 0)
        return winners / len(self._closed_trades)

    def summary(self) -> dict:
        """Retourne un résumé du portefeuille pour les logs et notifications."""
        return {
            "equity": self.equity,
            "initial_equity": self._initial_equity,
            "realized_pnl": round(self._realized_pnl, 4),
            "open_positions": len(self._positions),
            "total_unrealized_pnl": round(self.total_unrealized_pnl, 4),
            "closed_trades": len(self._closed_trades),
            "win_rate": round(self.win_rate, 4),
        }

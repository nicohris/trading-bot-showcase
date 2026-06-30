"""
Risk Manager — garde-fou central du bot.

Rôle : valider chaque signal avant qu'il devienne un ordre,
et calculer les paramètres de l'ordre (taille, stop, target).

Le RiskManager est le seul à pouvoir bloquer une entrée pour raisons de risque.
La stratégie propose, le RiskManager dispose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import structlog

from config.settings import load_trading_config
from core.enums import OrderSide, OrderType, SignalType
from core.exceptions import RiskViolationError
from core.models import AccountSnapshot, OrderRequest, Signal
from core.utils import generate_id, utcnow
from risk.position_sizer import PositionSizer

log = structlog.get_logger(__name__)


@dataclass
class DailyStats:
    """Statistiques journalières pour les garde-fous."""
    date: date = field(default_factory=date.today)
    starting_equity: float = 0.0
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    trades_count: int = 0

    @property
    def daily_pnl_pct(self) -> float:
        if self.starting_equity == 0:
            return 0.0
        return (self.realized_pnl / self.starting_equity) * 100

    def record_trade_result(self, pnl: float) -> None:
        """Met à jour les stats après la fermeture d'un trade."""
        self.realized_pnl += pnl
        self.trades_count += 1
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0  # Reset sur un trade gagnant


class RiskManager:
    """
    Valide les signaux et produit des OrderRequest conformes aux règles de risque.

    Règles appliquées :
    1. Vérification exposition totale max
    2. Vérification 1 position max par actif
    3. Vérification perte journalière max
    4. Vérification max pertes consécutives
    5. Calcul taille de position (fixed fractional)
    6. Calcul stop-loss ATR
    7. Calcul take profit (1R)

    Il ne communique pas avec l'exchange directement.
    Il reçoit un AccountSnapshot fourni par le runtime.
    """

    def __init__(self) -> None:
        cfg = load_trading_config()
        self._cfg = cfg.risk
        self._sizer = PositionSizer()
        self._daily_stats = DailyStats()
        self._log = log.bind(component="RiskManager")

    def validate_signal(
        self,
        signal: Signal,
        account: AccountSnapshot,
        reference_date: Optional[date] = None,
    ) -> OrderRequest:
        """
        Valide un signal d'entrée et retourne un OrderRequest prêt à exécuter.

        Args:
            signal: Signal produit par la stratégie
            account: État courant du compte
            reference_date: Date de référence pour le reset journalier.
                None = utilise date.today() (mode live/paper).
                Fournir la date de la bougie courante en mode replay pour simuler
                fidèlement les resets journaliers.

        Returns:
            OrderRequest avec tous les paramètres calculés

        Raises:
            RiskViolationError: Si une règle de risque est violée
        """
        if not signal.is_entry:
            raise ValueError(f"validate_signal called with non-entry signal: {signal.signal_type}")

        self._reset_daily_stats_if_new_day(account, reference_date=reference_date)

        # --- Garde-fous ---
        self._check_daily_loss_limit(account)
        self._check_consecutive_losses()
        self._check_max_open_positions(account)
        self._check_max_positions_per_symbol(signal.symbol, account)
        self._check_max_exposure(account)

        # --- Calculs ---
        stop_loss = self._sizer.calculate_stop_loss(
            entry_price=signal.close_price,
            atr=signal.atr,
            atr_multiplier=self._cfg.stop_atr_multiplier,
        )
        quantity = self._sizer.calculate_quantity(
            equity=account.total_equity,
            risk_pct=self._cfg.risk_per_trade_pct,
            entry_price=signal.close_price,
            stop_loss=stop_loss,
        )
        if signal.take_profit_price > 0:
            take_profit = signal.take_profit_price
        else:
            take_profit = self._sizer.calculate_take_profit(
                entry_price=signal.close_price,
                stop_loss=stop_loss,
                r_multiple=self._cfg.partial_take_at_r,
            )

        # Vérification que la quantité est valide
        if quantity <= 0:
            raise RiskViolationError(
                f"Calculated quantity is zero for {signal.symbol}",
                rule="position_size"
            )

        # Vérifier que le notionnel minimum est respecté (10 USDT min Binance)
        notional = quantity * signal.close_price
        if notional < 10:
            raise RiskViolationError(
                f"Order notional {notional:.2f} USDT below minimum for {signal.symbol}",
                rule="min_notional"
            )

        self._log.info(
            "Signal validated",
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            quantity=quantity,
            entry=signal.close_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            equity=account.total_equity,
        )

        return OrderRequest(
            symbol=signal.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal=signal,
            client_order_id=generate_id(prefix="bot"),
            full_exit_at_tp=signal.full_exit_at_tp,
        )

    def record_trade_closed(self, pnl: float) -> None:
        """
        Notifie le RiskManager qu'un trade s'est fermé.

        Appelé par le portfolio manager après chaque clôture.
        Maintient les statistiques journalières.
        """
        self._daily_stats.record_trade_result(pnl)
        self._log.info(
            "Trade recorded",
            pnl=pnl,
            consecutive_losses=self._daily_stats.consecutive_losses,
            daily_pnl_pct=self._daily_stats.daily_pnl_pct,
        )

    # -----------------------------------------------------------------------
    # Garde-fous privés
    # -----------------------------------------------------------------------

    def _check_daily_loss_limit(self, account: AccountSnapshot) -> None:
        """Bloque si la perte journalière dépasse le seuil configuré."""
        if self._daily_stats.daily_pnl_pct <= -self._cfg.max_daily_loss_pct:
            raise RiskViolationError(
                f"Daily loss limit reached: {self._daily_stats.daily_pnl_pct:.2f}% "
                f"(max: -{self._cfg.max_daily_loss_pct}%)",
                rule="max_daily_loss"
            )

    def _check_consecutive_losses(self) -> None:
        """Bloque si le nombre de pertes consécutives dépasse le seuil."""
        if self._daily_stats.consecutive_losses >= self._cfg.max_consecutive_losses:
            raise RiskViolationError(
                f"Max consecutive losses reached: {self._daily_stats.consecutive_losses}",
                rule="max_consecutive_losses"
            )

    def _check_max_open_positions(self, account: AccountSnapshot) -> None:
        """Bloque si le nombre total de positions ouvertes atteint le maximum portfolio."""
        n = len(account.open_positions)
        if n >= self._cfg.max_open_positions:
            raise RiskViolationError(
                f"Max open positions reached: {n}/{self._cfg.max_open_positions}",
                rule="max_open_positions"
            )

    def _check_max_positions_per_symbol(
        self, symbol: str, account: AccountSnapshot
    ) -> None:
        """Bloque s'il existe déjà une position ouverte sur ce symbole."""
        existing = [p for p in account.open_positions if p.symbol == symbol]
        if len(existing) >= self._cfg.max_positions_per_symbol:
            raise RiskViolationError(
                f"Already {len(existing)} position(s) open on {symbol}",
                rule="max_positions_per_symbol"
            )

    def _check_max_exposure(self, account: AccountSnapshot) -> None:
        """Bloque si l'exposition totale dépasse le seuil configuré."""
        if account.exposure_pct >= self._cfg.max_total_exposure_pct:
            raise RiskViolationError(
                f"Max total exposure reached: {account.exposure_pct:.2f}% "
                f"(max: {self._cfg.max_total_exposure_pct}%)",
                rule="max_total_exposure"
            )

    def _reset_daily_stats_if_new_day(
        self,
        account: AccountSnapshot,
        reference_date: date | None = None,
    ) -> None:
        """
        Réinitialise les stats si on est passé à un nouveau jour.

        En mode live/paper : reference_date=None → utilise date.today().
        En mode replay : reference_date = date de la bougie courante,
        ce qui permet de simuler fidèlement les resets journaliers.
        """
        today = reference_date if reference_date is not None else date.today()
        if self._daily_stats.date != today:
            self._log.info(
                "New trading day — resetting daily stats",
                previous_date=str(self._daily_stats.date),
                previous_pnl_pct=self._daily_stats.daily_pnl_pct,
            )
            self._daily_stats = DailyStats(
                date=today,
                starting_equity=account.total_equity,
            )

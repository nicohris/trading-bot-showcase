"""
Notificateur Telegram.

Envoie des messages formatés lors des événements trading importants.
Non-bloquant : une erreur de notification ne doit jamais bloquer le bot.

Utilise l'API synchrone de python-telegram-bot pour la simplicité (V1).
TODO: Passer en async pour les runtimes async si nécessaire.
"""

from __future__ import annotations

import structlog
import telegram
from telegram import Bot

from core.models import Position, Signal, Trade
from core.exceptions import NotificationError

log = structlog.get_logger(__name__)


def _format_pnl(pnl: float) -> str:
    """Formatte un PnL avec emoji selon le signe."""
    emoji = "✅" if pnl >= 0 else "❌"
    return f"{emoji} {pnl:+.2f} USDT"


class TelegramNotifier:
    """
    Envoie des notifications Telegram pour les événements du bot.

    Tous les appels sont wrappés dans un try/except pour ne jamais
    crasher le bot principal en cas de problème réseau.
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        if not bot_token or not chat_id:
            raise NotificationError("Telegram bot_token and chat_id are required")
        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._log = log.bind(component="TelegramNotifier")

    def _send(self, message: str) -> None:
        """Envoi bas niveau. Swallow les erreurs pour ne pas bloquer le bot."""
        try:
            import asyncio
            asyncio.run(self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="HTML",
            ))
        except Exception as e:
            self._log.warning("Telegram notification failed", error=str(e))

    def notify_trade_opened(self, signal: Signal, position: Position) -> None:
        """Notification à l'ouverture d'une position."""
        msg = (
            f"🚀 <b>Trade ouvert</b>\n"
            f"Symbol: <code>{signal.symbol}</code>\n"
            f"Setup: {signal.signal_type.value}\n"
            f"Entrée: <code>{position.entry_price:.4f}</code>\n"
            f"Stop: <code>{position.stop_loss:.4f}</code>\n"
            f"Target: <code>{position.take_profit:.4f}</code>\n"
            f"Quantité: <code>{position.quantity:.6f}</code>\n"
            f"Raison: {signal.reason}"
        )
        self._send(msg)

    def notify_trade_closed(self, trade: Trade) -> None:
        """Notification à la fermeture d'un trade."""
        r = f"{trade.r_multiple:.2f}R" if trade.r_multiple is not None else "N/A"
        msg = (
            f"🏁 <b>Trade fermé</b>\n"
            f"Symbol: <code>{trade.symbol}</code>\n"
            f"Entrée: <code>{trade.entry_price:.4f}</code>\n"
            f"Sortie: <code>{trade.exit_price:.4f}</code>\n"
            f"PnL net: {_format_pnl(trade.net_pnl)}\n"
            f"R-multiple: <code>{r}</code>"
        )
        self._send(msg)

    def notify_risk_violation(self, rule: str, message: str) -> None:
        """Notification quand le risk manager bloque une action."""
        msg = (
            f"⚠️ <b>Risk Violation</b>\n"
            f"Règle: <code>{rule}</code>\n"
            f"Message: {message}"
        )
        self._send(msg)

    def notify_daily_summary(self, portfolio_summary: dict) -> None:
        """Résumé journalier du portfolio."""
        pnl = portfolio_summary.get("total_realized_pnl", 0)
        msg = (
            f"📊 <b>Résumé journalier</b>\n"
            f"Trades fermés: {portfolio_summary.get('closed_trades', 0)}\n"
            f"PnL réalisé: {_format_pnl(pnl)}\n"
            f"PnL flottant: {_format_pnl(portfolio_summary.get('total_unrealized_pnl', 0))}\n"
            f"Win rate: {portfolio_summary.get('win_rate', 0):.1%}\n"
            f"Positions ouvertes: {portfolio_summary.get('open_positions', 0)}"
        )
        self._send(msg)

    def notify_error(self, context: str, error: str) -> None:
        """Notification d'erreur critique."""
        msg = (
            f"🔴 <b>Erreur bot</b>\n"
            f"Contexte: {context}\n"
            f"Erreur: <code>{error[:200]}</code>"  # Tronquer les messages trop longs
        )
        self._send(msg)

    def notify_bot_started(self, mode: str, symbols: list[str]) -> None:
        """Confirmation de démarrage."""
        msg = (
            f"🤖 <b>Bot démarré</b>\n"
            f"Mode: <code>{mode}</code>\n"
            f"Symboles: {', '.join(symbols)}"
        )
        self._send(msg)

    def notify_bot_stopped(self) -> None:
        """Notification d'arrêt propre."""
        self._send("🛑 <b>Bot arrêté proprement</b>")

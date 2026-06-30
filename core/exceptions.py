"""
Hiérarchie d'exceptions du bot.

Toutes les exceptions métier héritent de BotError pour permettre
un catch global propre au niveau du runtime.
"""


class BotError(Exception):
    """Exception racine du bot. Toutes les exceptions métier en héritent."""


class ConfigError(BotError):
    """Configuration invalide ou manquante."""


class DataError(BotError):
    """Erreur lors de la récupération ou du traitement des données marché."""


class ExchangeError(BotError):
    """Erreur de communication avec l'exchange."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code  # Code d'erreur Binance si disponible


class InsufficientFundsError(ExchangeError):
    """Fonds insuffisants pour exécuter l'ordre."""


class RiskViolationError(BotError):
    """Le risk manager a bloqué une action."""

    def __init__(self, message: str, rule: str = "") -> None:
        super().__init__(message)
        self.rule = rule  # Quelle règle a été violée


class StrategyError(BotError):
    """Erreur interne à la stratégie (données insuffisantes, bug logique)."""


class ExecutionError(BotError):
    """Erreur lors de l'exécution d'un ordre."""


class StorageError(BotError):
    """Erreur de persistance (lecture/écriture base de données)."""


class NotificationError(BotError):
    """Erreur lors de l'envoi d'une notification (non critique)."""

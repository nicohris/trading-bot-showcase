"""
Interface abstraite pour un exchange.

La stratégie, le risk manager et l'executor n'ont jamais de dépendance
directe sur Binance. Ils parlent uniquement à ExchangeInterface.
Cela permet d'ajouter un autre exchange (Bybit, OKX) sans toucher au reste.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import AccountSnapshot, Order, OrderRequest


class ExchangeInterface(ABC):
    """
    Contrat que tout exchange doit respecter.

    Méthodes séparées en deux groupes :
    - Lecture (état du compte, ordres, positions)
    - Écriture (placement/annulation d'ordres)
    """

    # -----------------------------------------------------------------------
    # Lecture
    # -----------------------------------------------------------------------

    @abstractmethod
    def get_account_snapshot(self) -> AccountSnapshot:
        """
        Retourne l'état complet du compte : balance + positions ouvertes.

        Utilisé par le RiskManager avant chaque décision.
        """
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Retourne la liste des ordres ouverts (tous symboles ou un seul)."""
        ...

    @abstractmethod
    def get_order(self, symbol: str, order_id: str) -> Order:
        """Récupère un ordre spécifique par son ID."""
        ...

    # -----------------------------------------------------------------------
    # Écriture
    # -----------------------------------------------------------------------

    @abstractmethod
    def place_order(self, request: OrderRequest) -> Order:
        """
        Envoie un ordre à l'exchange.

        Args:
            request: L'ordre à placer (validé par le RiskManager)

        Returns:
            L'Order confirmé avec son ID exchange et son statut.

        Raises:
            ExchangeError: En cas d'erreur API
            InsufficientFundsError: Si le compte n'a pas les fonds
        """
        ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> Order:
        """Annule un ordre ouvert. Retourne l'ordre mis à jour."""
        ...

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> list[Order]:
        """Annule tous les ordres ouverts sur un symbole."""
        ...

    # -----------------------------------------------------------------------
    # Méthodes utilitaires (peuvent avoir une implémentation par défaut)
    # -----------------------------------------------------------------------

    def get_symbol_info(self, symbol: str) -> dict:
        """
        Retourne les règles de trading pour un symbole (step sizes, min notional...).

        TODO: Retourner un objet typé SymbolInfo plutôt qu'un dict.
        """
        raise NotImplementedError

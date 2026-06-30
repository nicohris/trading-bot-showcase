"""
Interface abstraite pour l'exécution des ordres.

Live et Paper partagent la même interface.
Le reste du bot ne fait jamais la distinction — il parle à ExecutorBase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import Order, OrderRequest


class ExecutorBase(ABC):
    """
    Interface d'exécution des ordres.

    Responsabilités :
    - Recevoir un OrderRequest validé par le RiskManager
    - L'exécuter (live → exchange, paper → simulation)
    - Retourner l'Order résultant
    - Gérer les erreurs d'exécution

    Ce qu'il NE fait PAS :
    - Valider le risque (rôle du RiskManager)
    - Mettre à jour le portfolio (rôle du PortfolioManager)
    - Décider si on trade (rôle de la stratégie)
    """

    @abstractmethod
    def execute(self, request: OrderRequest) -> Order:
        """
        Exécute un ordre.

        Args:
            request: OrderRequest validé, prêt à être envoyé

        Returns:
            Order avec statut et prix d'exécution

        Raises:
            ExecutionError: Si l'exécution échoue
        """
        ...

    @abstractmethod
    def cancel(self, symbol: str, order_id: str) -> Order:
        """Annule un ordre ouvert par son ID."""
        ...

    @abstractmethod
    def get_order_status(self, symbol: str, order_id: str) -> Order:
        """Récupère le statut actuel d'un ordre."""
        ...

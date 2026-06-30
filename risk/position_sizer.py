"""
Calcul de la taille de position.

Logique pure, sans dépendances exchange. Reçoit des nombres, retourne des nombres.
Toujours tester ce module en isolation — les calculs doivent être déterministes.
"""

from __future__ import annotations

import math


class PositionSizer:
    """
    Calcule la quantité à acheter en fonction du risque défini.

    Méthode : Fixed Fractional Risk
    - On définit combien on est prêt à perdre en $ sur ce trade
    - On déduit la taille en fonction de la distance au stop-loss

    Exemple :
    - Capital : 10 000 USDT
    - Risque par trade : 1% → risque_$ = 100 USDT
    - Prix entrée : 40 000
    - Stop-loss : 39 000 → distance = 1 000
    - Taille = 100 / 1 000 = 0.1 BTC

    Aucune martingale, aucune logique de renforcement ici.
    """

    def calculate_quantity(
        self,
        equity: float,
        risk_pct: float,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        """
        Calcule la quantité à acheter.

        Args:
            equity: Capital total en USDT
            risk_pct: % du capital à risquer (ex: 1.0 pour 1%)
            entry_price: Prix d'entrée estimé
            stop_loss: Niveau de stop-loss

        Returns:
            Quantité en unité de base (ex: BTC), arrondie à 6 décimales.
            Retourne 0.0 si le calcul est invalide.
        """
        if equity <= 0 or entry_price <= 0:
            return 0.0

        distance = abs(entry_price - stop_loss)
        if distance == 0:
            return 0.0

        risk_amount = equity * (risk_pct / 100)
        quantity = risk_amount / distance

        # Arrondi à 6 décimales (précision standard Binance BTC)
        return round(quantity, 6)

    def calculate_stop_loss(
        self,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 1.5,
        side: str = "BUY",
    ) -> float:
        """
        Calcule le niveau de stop-loss basé sur l'ATR.

        Args:
            entry_price: Prix d'entrée
            atr: ATR courant (en USDT)
            atr_multiplier: Multiplicateur ATR pour le stop (défaut 1.5)
            side: 'BUY' ou 'SELL'

        Returns:
            Prix du stop-loss.
        """
        if atr <= 0:
            raise ValueError("ATR must be positive to calculate stop-loss")

        distance = atr * atr_multiplier
        if side == "BUY":
            return round(entry_price - distance, 8)
        else:
            return round(entry_price + distance, 8)

    def calculate_take_profit(
        self,
        entry_price: float,
        stop_loss: float,
        r_multiple: float = 1.0,
    ) -> float:
        """
        Calcule un niveau de take profit en multiples de R.

        Args:
            entry_price: Prix d'entrée
            stop_loss: Niveau de stop-loss
            r_multiple: Multiple de R pour la cible (1.0 = 1R)

        Returns:
            Prix du take profit.
        """
        risk = abs(entry_price - stop_loss)
        return round(entry_price + risk * r_multiple, 8)

    def calculate_trailing_stop(
        self,
        current_price: float,
        atr: float,
        atr_multiplier: float = 1.5,
        side: str = "BUY",
        current_trailing: float | None = None,
    ) -> float:
        """
        Calcule le nouveau niveau de trailing stop.

        Ne fait jamais reculer le stop (ratchet mécanique).

        Args:
            current_price: Prix actuel
            atr: ATR courant
            atr_multiplier: Multiplicateur ATR pour le trailing
            side: 'BUY' ou 'SELL'
            current_trailing: Stop actuel (pour ne pas le faire reculer)

        Returns:
            Nouveau niveau de trailing stop.
        """
        new_stop = round(current_price - atr * atr_multiplier, 8)

        if current_trailing is None:
            return new_stop

        if side == "BUY":
            # On ne peut que monter le stop
            return max(new_stop, current_trailing)
        else:
            # On ne peut que descendre le stop
            return min(new_stop, current_trailing)

    def calculate_risk_amount(self, equity: float, risk_pct: float) -> float:
        """Calcule le montant en $ risqué sur un trade."""
        return equity * (risk_pct / 100)

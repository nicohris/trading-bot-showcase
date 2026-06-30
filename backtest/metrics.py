"""
Métriques de performance pour l'évaluation d'un backtest.

Fonctions pures : reçoivent des trades, retournent des métriques.
Pas de dépendances sur l'exchange ou la stratégie.
"""

from __future__ import annotations

import math
from typing import Sequence

from core.models import Trade


class BacktestMetrics:
    """
    Calcule les métriques de performance d'un backtest.

    Métriques couvertes :
    - Rendement total
    - Win rate
    - Profit factor
    - Max drawdown
    - Sharpe ratio simplifié
    - Moyenne R multiple
    - Nombre de trades, winners, losers
    """

    def __init__(self, trades: Sequence[Trade], initial_capital: float) -> None:
        self._trades = list(trades)
        self._initial_capital = initial_capital

    def compute(self) -> dict:
        """Retourne toutes les métriques sous forme de dictionnaire."""
        if not self._trades:
            return {"error": "No closed trades to compute metrics"}

        pnls = [t.net_pnl for t in self._trades]
        r_multiples = [t.r_multiple for t in self._trades if t.r_multiple is not None]

        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        return {
            "total_trades": len(self._trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(self._trades) if self._trades else 0,
            "total_pnl": sum(pnls),
            "total_return_pct": (sum(pnls) / self._initial_capital) * 100,
            "avg_win": sum(winners) / len(winners) if winners else 0,
            "avg_loss": sum(losers) / len(losers) if losers else 0,
            "profit_factor": self._profit_factor(winners, losers),
            "max_drawdown_pct": self._max_drawdown(pnls),
            "avg_r_multiple": sum(r_multiples) / len(r_multiples) if r_multiples else 0,
            "expectancy": sum(pnls) / len(pnls) if pnls else 0,
        }

    def _profit_factor(self, winners: list[float], losers: list[float]) -> float:
        """Profit Factor = somme gains / somme pertes (abs)."""
        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def _max_drawdown(self, pnls: list[float]) -> float:
        """
        Max drawdown en % du capital initial.

        Calculé sur la courbe cumulée des PnL (simplifié, hors equity curve complète).
        """
        if not pnls:
            return 0.0

        cumulative = self._initial_capital
        peak = cumulative
        max_dd = 0.0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak * 100
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def print_summary(self) -> None:
        """Affiche un résumé formaté des métriques dans la console."""
        metrics = self.compute()
        print("\n" + "=" * 50)
        print("BACKTEST RESULTS")
        print("=" * 50)
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"  {key:<25} {value:>10.2f}")
            else:
                print(f"  {key:<25} {value:>10}")
        print("=" * 50 + "\n")

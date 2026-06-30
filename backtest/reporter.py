"""
BacktestReporter — exports et affichage des résultats de backtest.

Responsabilités :
- Export trades → CSV (outputs/)
- Export résumé → JSON (outputs/)
- Affichage console lisible (résumé formaté)
- Compatible avec BacktestResult (single symbol) et une liste de résultats (multi-symbol)
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from backtest.engine import BacktestResult
from core.models import Trade


class BacktestReporter:
    """
    Exporte et affiche les résultats d'un ou plusieurs backtests.

    Usage :
        reporter = BacktestReporter(output_dir="outputs")
        trades_path, summary_path = reporter.export(result)
        reporter.print_summary(result)
    """

    def __init__(self, output_dir: str = "outputs") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Export
    # -----------------------------------------------------------------------

    def export(self, result: BacktestResult) -> tuple[Path, Path]:
        """
        Exporte les trades et le résumé vers des fichiers.

        Returns:
            (trades_csv_path, summary_json_path)
        """
        slug = f"{result.symbol}_{result.start_date}_{result.end_date}"
        trades_path = self._export_trades_csv(result, slug)
        summary_path = self._export_summary_json(result, slug)
        return trades_path, summary_path

    def _export_trades_csv(self, result: BacktestResult, slug: str) -> Path:
        """Exporte la liste des trades en CSV."""
        path = self._output_dir / f"trades_{slug}.csv"
        fieldnames = [
            "symbol",
            "side",
            "setup_type",
            "entry_time",
            "entry_price",
            "initial_stop",
            "take_profit_1r",
            "exit_time",
            "exit_price",
            "exit_reason",
            "quantity",
            "partial_taken",
            "pnl_after_exit_fees",
            "fees_total",
            "r_multiple",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for trade in result.trades:
                writer.writerow(_trade_to_row(trade))

        return path

    def _export_summary_json(self, result: BacktestResult, slug: str) -> Path:
        """Exporte le résumé du backtest en JSON."""
        path = self._output_dir / f"summary_{slug}.json"

        metrics = result.metrics.copy()
        # Formater les floats pour la lisibilité
        for key, val in metrics.items():
            if isinstance(val, float):
                metrics[key] = round(val, 4)

        data = {
            "symbol": result.symbol,
            "period": {
                "start": result.start_date,
                "end": result.end_date,
            },
            "capital": {
                "initial": result.initial_capital,
                "final": round(result.final_equity, 2),
                "return_pct": round(result.total_return_pct, 4),
                "pnl": round(result.final_equity - result.initial_capital, 2),
            },
            "metrics": metrics,
            "trade_count": len(result.trades),
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return path

    # -----------------------------------------------------------------------
    # Affichage console
    # -----------------------------------------------------------------------

    def print_summary(self, result: BacktestResult) -> None:
        """Affiche un résumé formaté et lisible dans la console."""
        m = result.metrics
        pnl = result.final_equity - result.initial_capital
        pnl_sign = "+" if pnl >= 0 else ""
        ret_sign = "+" if result.total_return_pct >= 0 else ""

        _sep = "=" * 56

        print()
        print(_sep)
        print(f"  BACKTEST — {result.symbol}")
        print(f"  Période : {result.start_date} → {result.end_date}")
        print(_sep)
        print(
            f"  Capital       {result.initial_capital:>12,.2f} USDT"
            f"  →  {result.final_equity:>12,.2f} USDT"
        )
        print(
            f"  Rendement     {ret_sign}{result.total_return_pct:>10.2f}%"
            f"     ({pnl_sign}{pnl:>10,.2f} USDT)"
        )
        print(_sep)

        if isinstance(m.get("error"), str):
            print(f"  ⚠  {m['error']}")
        else:
            n = m.get("total_trades", 0)
            w = m.get("winners", 0)
            l_ = m.get("losers", 0)
            win_rate = m.get("win_rate", 0) * 100

            print(f"  Trades        {n:>10}         ({w}W / {l_}L)")
            print(f"  Win rate      {win_rate:>10.1f}%")

            pf = m.get("profit_factor", 0)
            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            print(f"  Profit factor {pf_str:>10}")

            dd = m.get("max_drawdown_pct", 0)
            print(f"  Max drawdown  {-dd:>10.2f}%")

            avg_r = m.get("avg_r_multiple", 0)
            avg_r_sign = "+" if avg_r >= 0 else ""
            print(f"  Avg R-multiple{avg_r_sign}{avg_r:>9.2f}R")

            exp = m.get("expectancy", 0)
            exp_sign = "+" if exp >= 0 else ""
            print(f"  Expectancy    {exp_sign}{exp:>10.2f} USDT/trade")

            # Total fees (approximation depuis les trades)
            total_fees = sum(t.commission_total for t in result.trades)
            print(f"  Fees totaux   {-total_fees:>10.2f} USDT")

        print(_sep)
        print()

    def print_combined_summary(self, results: list[BacktestResult]) -> None:
        """Affiche un résumé combiné pour un backtest multi-symbole."""
        if not results:
            return

        _sep = "=" * 56
        print()
        print(_sep)
        print("  BACKTEST COMBINÉ")
        print(_sep)

        total_pnl = sum(r.final_equity - r.initial_capital for r in results)
        total_capital = sum(r.initial_capital for r in results)
        total_trades = sum(len(r.trades) for r in results)
        total_return_pct = (total_pnl / total_capital * 100) if total_capital else 0.0

        pnl_sign = "+" if total_pnl >= 0 else ""
        ret_sign = "+" if total_return_pct >= 0 else ""

        for r in results:
            r_pnl = r.final_equity - r.initial_capital
            r_sign = "+" if r_pnl >= 0 else ""
            ret_r_sign = "+" if r.total_return_pct >= 0 else ""
            n = len(r.trades)
            print(
                f"  {r.symbol:<10} {ret_r_sign}{r.total_return_pct:>7.2f}%"
                f"   {r_sign}{r_pnl:>10,.2f} USDT   ({n} trades)"
            )

        print(_sep)
        print(f"  TOTAL        {ret_sign}{total_return_pct:>7.2f}%   {pnl_sign}{total_pnl:>10,.2f} USDT   ({total_trades} trades)")
        print(_sep)
        print()


# ---------------------------------------------------------------------------
# Helper interne
# ---------------------------------------------------------------------------


def _trade_to_row(trade: Trade) -> dict:
    """Convertit un Trade en dict pour le CSV."""

    def _fmt_dt(dt) -> str:
        if dt is None:
            return ""
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if hasattr(dt, "strftime") else str(dt)

    def _fmt_f(v, decimals: int = 4) -> str:
        if v is None:
            return ""
        return f"{v:.{decimals}f}"

    setup = trade.setup_type.value if trade.setup_type else ""
    r_mult = trade.r_multiple
    r_str = f"{r_mult:.2f}" if r_mult is not None else ""

    return {
        "symbol": trade.symbol,
        "side": trade.side.value,
        "setup_type": setup,
        "entry_time": _fmt_dt(trade.entry_time),
        "entry_price": _fmt_f(trade.entry_price),
        "initial_stop": _fmt_f(trade.stop_loss),
        "take_profit_1r": _fmt_f(trade.take_profit_1r),
        "exit_time": _fmt_dt(trade.exit_time),
        "exit_price": _fmt_f(trade.exit_price),
        "exit_reason": trade.exit_reason,
        "quantity": _fmt_f(trade.entry_quantity, 6),
        "partial_taken": str(trade.partial_taken).lower(),
        "pnl_after_exit_fees": _fmt_f(trade.realized_pnl),
        "fees_total": _fmt_f(trade.commission_total),
        "r_multiple": r_str,
    }

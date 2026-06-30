"""
Backtest Engine — moteur de simulation historique.

Principe "Walk-Forward" bougie par bougie :
1. Pour chaque bougie fermée (du plus ancien au plus récent)
2. Si un signal était en attente : entrer à l'open de cette bougie
3. Gérer les positions ouvertes (stops, targets, trailing) sur cette bougie
4. Alimenter la stratégie avec les données disponibles jusqu'à ce point
5. Si signal d'entrée : le mettre en attente pour la prochaine bougie

Convention sans lookahead :
- Signal généré à la clôture de la bougie N
- Entrée à l'open de la bougie N+1 (avec slippage)
- Stops/targets vérifiés sur la bougie N+1 et suivantes

Convention intrabar conservative :
- Si stop ET 1R target tous les deux touchés dans la même bougie
  AVANT que la prise partielle ait été effectuée → le stop l'emporte
  (cas le plus défavorable, principe de prudence).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import structlog

from backtest.metrics import BacktestMetrics
from config.settings import load_trading_config
from core.enums import OrderSide, OrderType
from core.exceptions import RiskViolationError, StrategyError
from core.models import AccountSnapshot, Candle, OrderRequest, Trade
from core.utils import utcnow
from data.fetcher import BacktestDataProvider
from execution.paper import PaperExecutor
from portfolio.manager import PortfolioManager
from risk.manager import RiskManager
from strategy.base import StrategyBase, StrategyContext
from strategy.indicators import add_atr

log = structlog.get_logger(__name__)


@dataclass
class BacktestResult:
    """Résultats complets d'un backtest."""
    symbol: str
    start_date: str
    end_date: str
    initial_capital: float
    final_equity: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return ((self.final_equity - self.initial_capital) / self.initial_capital) * 100


class BacktestEngine:
    """
    Moteur de backtest walk-forward.

    Simule le comportement du bot sur des données historiques.
    Partage la stratégie et le risk manager avec le runtime live —
    ce qui garantit que ce qu'on teste est ce qui tourne en production.

    Seuls le DataProvider (BacktestDataProvider) et l'Executor (PaperExecutor)
    diffèrent du mode live.
    """

    def __init__(
        self,
        strategy: StrategyBase,
        risk_manager: RiskManager,
    ) -> None:
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._cfg = load_trading_config()
        self._log = log.bind(component="BacktestEngine")

    def run(
        self,
        symbol: str,
        trend_candles: list[Candle],    # 4h — données complètes
        exec_candles: list[Candle],     # 1h — données complètes
    ) -> BacktestResult:
        """
        Lance un backtest complet sur un symbole.

        Args:
            symbol: Ex. 'BTCUSDT'
            trend_candles: Bougies 4h historiques, triées chronologiquement
            exec_candles: Bougies 1h historiques, triées chronologiquement

        Returns:
            BacktestResult avec tous les trades et métriques
        """
        initial_capital = self._cfg.backtest.initial_capital
        exec_tf = self._cfg.execution_timeframe
        trend_tf = self._cfg.trend_timeframe

        self._log.info(
            "Starting backtest",
            symbol=symbol,
            trend_candles=len(trend_candles),
            exec_candles=len(exec_candles),
            initial_capital=initial_capital,
        )

        # --- Composants de simulation ---
        data_provider = BacktestDataProvider()
        data_provider.load(symbol, trend_tf, trend_candles)
        data_provider.load(symbol, exec_tf, exec_candles)

        executor = PaperExecutor(
            data_provider=data_provider,
            fee_rate=self._cfg.backtest.fee_rate,
            slippage_pct=self._cfg.backtest.slippage_pct,
        )
        portfolio = PortfolioManager(initial_equity=initial_capital)

        equity_curve: list[float] = [initial_capital]
        candle_count = 0

        # pending_orders : signal validé en attente d'exécution à l'open suivant
        # symbol → OrderRequest
        pending_orders: dict[str, OrderRequest] = {}

        # --- Boucle walk-forward ---
        while data_provider.advance(symbol):
            exec_candles_now = data_provider.get_candles(symbol, exec_tf, limit=300)
            if not exec_candles_now:
                continue

            current_candle = exec_candles_now[-1]
            candle_count += 1

            # 1. Entrée en position (signal de la bougie précédente → open courant)
            if symbol in pending_orders:
                req = pending_orders.pop(symbol)
                self._enter_position(
                    symbol=symbol,
                    order_request=req,
                    open_price=current_candle.open,
                    portfolio=portfolio,
                    executor=executor,
                )

            # 2. Gestion des positions ouvertes (stops, targets, trailing)
            self._manage_open_positions(
                symbol=symbol,
                portfolio=portfolio,
                executor=executor,
                data_provider=data_provider,
                current_candle=current_candle,
            )

            # 3. Mise à jour de la courbe de capital
            equity = portfolio.equity + portfolio.total_unrealized_pnl
            equity_curve.append(equity)

            # 4. Génération du signal si pas de position en cours ni en attente
            if portfolio.has_position(symbol) or symbol in pending_orders:
                continue

            context = StrategyContext(
                symbol=symbol,
                trend_candles=data_provider.get_candles(symbol, trend_tf, limit=300),
                exec_candles=exec_candles_now,
            )

            try:
                signal = self._strategy.generate_signal(context)
            except StrategyError as e:
                self._log.warning("Strategy error", error=str(e))
                continue

            if signal.is_none:
                continue

            # 5. Validation risque (sur les données actuelles)
            account = AccountSnapshot(
                timestamp=current_candle.timestamp,
                total_equity=portfolio.equity + portfolio.total_unrealized_pnl,
                available_balance=portfolio.equity,
                open_positions=portfolio.open_positions,
            )
            try:
                order_request = self._risk_manager.validate_signal(signal, account)
                pending_orders[symbol] = order_request
                self._log.info(
                    "Signal queued → entry next open",
                    symbol=symbol,
                    signal_type=signal.signal_type.value,
                    setup=signal.setup_type.value if signal.setup_type else "",
                    entry_ref=round(signal.close_price, 2),
                    atr=round(signal.atr, 2),
                    stop=order_request.stop_loss,
                    target_1r=order_request.take_profit,
                    qty=order_request.quantity,
                )
            except RiskViolationError as e:
                self._log.debug("Risk blocked signal", symbol=symbol, rule=e.rule)

        # --- Résultats finaux ---
        final_equity = portfolio.equity + portfolio.total_unrealized_pnl
        metrics_calc = BacktestMetrics(portfolio.closed_trades, initial_capital)
        result = BacktestResult(
            symbol=symbol,
            start_date=str(exec_candles[0].timestamp.date()) if exec_candles else "",
            end_date=str(exec_candles[-1].timestamp.date()) if exec_candles else "",
            initial_capital=initial_capital,
            final_equity=final_equity,
            trades=portfolio.closed_trades,
            equity_curve=equity_curve,
            metrics=metrics_calc.compute(),
        )

        self._log.info(
            "Backtest complete",
            symbol=symbol,
            total_trades=len(portfolio.closed_trades),
            return_pct=round(result.total_return_pct, 2),
            candles_processed=candle_count,
            final_equity=round(final_equity, 2),
        )
        return result

    # -----------------------------------------------------------------------
    # Entrée en position
    # -----------------------------------------------------------------------

    def _enter_position(
        self,
        symbol: str,
        order_request: OrderRequest,
        open_price: float,
        portfolio: PortfolioManager,
        executor: PaperExecutor,
    ) -> None:
        """
        Exécute une entrée en position à l'open de la bougie courante.

        L'ordre a été validé par le RiskManager à la clôture de la bougie précédente.
        L'entrée se fait au prix d'open avec slippage adverse simulé.
        """
        order = executor.execute_at_price(
            symbol=symbol,
            side=order_request.side,
            quantity=order_request.quantity,
            price=open_price,
            apply_slippage=True,
        )

        # Risque réel basé sur le prix de fill (pas le close de référence du signal)
        fill_price = order.avg_fill_price or open_price
        stop = order_request.stop_loss
        initial_risk = (
            (fill_price - stop) * order.filled_quantity
            if stop is not None
            else 0.0
        )

        portfolio.open_position(
            order=order,
            stop_loss=stop,
            take_profit=order_request.take_profit,
            initial_stop=stop,
            take_profit_1r=order_request.take_profit,
            initial_risk=initial_risk,
        )

        # Propager le flag de sortie totale (ex: mean reversion)
        if order_request.full_exit_at_tp:
            opened = portfolio.get_position(symbol)
            if opened is not None:
                opened.full_exit_at_tp = True

        self._log.info(
            "Position entered",
            symbol=symbol,
            fill_price=round(fill_price, 4),
            open_price=open_price,
            qty=order.filled_quantity,
            stop_loss=stop,
            take_profit_1r=order_request.take_profit,
        )

    # -----------------------------------------------------------------------
    # Gestion des positions ouvertes
    # -----------------------------------------------------------------------

    def _manage_open_positions(
        self,
        symbol: str,
        portfolio: PortfolioManager,
        executor: PaperExecutor,
        data_provider: BacktestDataProvider,
        current_candle: Candle,
    ) -> None:
        """
        Vérifie et gère les stops/targets sur la bougie courante.

        Convention intrabar pour les longs :
        - stop_hit   : low de la bougie ≤ niveau de stop
        - target_hit : high de la bougie ≥ niveau 1R take-profit

        Ordre de priorité :
        1. [Conservative] stop + 1R tous les deux touchés avant partial → stop gagne
        2. Prise partielle 1R → break-even → trailing actif
        3. [Après partial] stop ou trailing touchés → sortie du solde
        4. [Avant partial] stop touché → sortie totale
        """
        position = portfolio.get_position(symbol)
        if position is None:
            return

        # Mise à jour du prix pour le PnL non réalisé
        portfolio.update_position_price(symbol, current_candle.close)

        low = current_candle.low
        high = current_candle.high

        stop = position.stop_loss
        target_1r = position.take_profit_1r

        stop_hit = stop is not None and low <= stop
        target_hit = target_1r is not None and high >= target_1r

        # ----------------------------------------------------------------
        # Cas 1 : Conservative intrabar — stop ET 1R touchés avant partial
        # ----------------------------------------------------------------
        if stop_hit and target_hit and not position.partial_taken:
            self._log.debug(
                "Intrabar ambiguity: stop takes priority",
                symbol=symbol,
                low=low,
                high=high,
                stop=stop,
                target_1r=target_1r,
            )
            position.exit_reason = "stop_loss_intrabar_priority"
            self._exit_full(symbol, stop, portfolio, executor)
            return

        # ----------------------------------------------------------------
        # Cas 2a : Sortie totale au TP (full_exit_at_tp=True — ex: mean reversion)
        # ----------------------------------------------------------------
        if target_hit and not position.partial_taken and position.full_exit_at_tp:
            position.exit_reason = "take_profit"
            self._exit_full(symbol, target_1r, portfolio, executor)
            return

        # ----------------------------------------------------------------
        # Cas 2 : Prise partielle à 1R (si pas encore effectuée)
        # ----------------------------------------------------------------
        if target_hit and not position.partial_taken:
            partial_qty = round(
                position.initial_quantity * (self._cfg.risk.partial_take_pct / 100),
                6,
            )
            if partial_qty > 0 and partial_qty < position.quantity:
                self._exit_partial(symbol, target_1r, partial_qty, portfolio, executor)

            # Break-even : stop-loss → prix d'entrée
            portfolio.set_break_even(symbol)

            # Initialisation du trailing stop
            atr = self._get_current_atr(symbol, data_provider)
            position = portfolio.get_position(symbol)
            if position is not None and atr is not None:
                trailing = current_candle.close - atr * self._cfg.risk.trailing_atr_multiplier
                position.trailing_stop = trailing
                position.trailing_active = True
                self._log.info(
                    "Trailing stop initialized",
                    symbol=symbol,
                    trailing_stop=round(trailing, 4),
                    close=current_candle.close,
                    atr=round(atr, 4),
                )

            # Vérifier si le stop est aussi touché sur cette même bougie
            # (après la prise partielle, le solde peut encore être stoppé)
            position = portfolio.get_position(symbol)
            if position is not None and stop_hit:
                effective_stop = self._effective_stop(position)
                if effective_stop is not None and low <= effective_stop:
                    position.exit_reason = "break_even_stop_after_partial"
                    self._exit_full(symbol, effective_stop, portfolio, executor)
            return

        # ----------------------------------------------------------------
        # Cas 3 : Position après prise partielle — trailing/break-even
        # ----------------------------------------------------------------
        if position.partial_taken and position.trailing_active:
            effective_stop = self._effective_stop(position)
            if effective_stop is not None and low <= effective_stop:
                trailing = position.trailing_stop
                be_stop = position.stop_loss  # = entry_price (break-even)
                if trailing is not None and trailing >= (be_stop or 0):
                    position.exit_reason = "trailing_stop"
                else:
                    position.exit_reason = "break_even_stop"
                self._exit_full(symbol, effective_stop, portfolio, executor)
                return

            # Mise à jour du trailing stop (ratchet — ne recule jamais)
            atr = self._get_current_atr(symbol, data_provider)
            position = portfolio.get_position(symbol)
            if position is not None and atr is not None:
                new_trailing = current_candle.close - atr * self._cfg.risk.trailing_atr_multiplier
                if position.trailing_stop is None or new_trailing > position.trailing_stop:
                    position.trailing_stop = new_trailing
                    self._log.debug(
                        "Trailing stop updated",
                        symbol=symbol,
                        trailing_stop=round(new_trailing, 4),
                    )
            return

        # ----------------------------------------------------------------
        # Cas 4 : Stop-loss classique (avant prise partielle)
        # ----------------------------------------------------------------
        if stop_hit:
            position.exit_reason = "stop_loss"
            self._exit_full(symbol, stop, portfolio, executor)

    # -----------------------------------------------------------------------
    # Helpers d'exécution
    # -----------------------------------------------------------------------

    def _exit_full(
        self,
        symbol: str,
        price: float,
        portfolio: PortfolioManager,
        executor: PaperExecutor,
    ) -> None:
        """Ferme totalement une position au prix indiqué (sans slippage sur les stops)."""
        position = portfolio.get_position(symbol)
        if position is None:
            return

        order = executor.execute_at_price(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=position.quantity,
            price=price,
            apply_slippage=False,  # Sorties à prix fixé (stop/target)
        )
        trade = portfolio.close_position(symbol, order, partial=False)
        if trade:
            self._risk_manager.record_trade_closed(trade.realized_pnl)

    def _exit_partial(
        self,
        symbol: str,
        price: float,
        qty: float,
        portfolio: PortfolioManager,
        executor: PaperExecutor,
    ) -> None:
        """Ferme partiellement une position au prix indiqué."""
        order = executor.execute_at_price(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=qty,
            price=price,
            apply_slippage=False,
        )
        portfolio.close_position(symbol, order, partial=True)
        self._log.info(
            "Partial take at 1R",
            symbol=symbol,
            price=round(price, 4),
            qty=qty,
        )

    # -----------------------------------------------------------------------
    # ATR courant et stop effectif
    # -----------------------------------------------------------------------

    def _get_current_atr(
        self,
        symbol: str,
        data_provider: BacktestDataProvider,
    ) -> float | None:
        """
        Calcule l'ATR courant à partir des bougies d'exécution disponibles.

        Retourne None si les données sont insuffisantes.
        """
        period = self._cfg.strategy.atr_period
        candles = data_provider.get_candles(
            symbol, self._cfg.execution_timeframe, limit=period + 5
        )
        if len(candles) <= period:
            return None

        df = pd.DataFrame(
            [{"high": c.high, "low": c.low, "close": c.close} for c in candles]
        )
        df = add_atr(df, period)
        atr_col = f"atr_{period}"

        last_atr = df[atr_col].iloc[-1]
        if pd.isna(last_atr):
            return None
        return float(last_atr)

    @staticmethod
    def _effective_stop(position: "Position") -> float | None:  # type: ignore[name-defined]
        """
        Retourne le niveau de stop effectif pour la position (après prise partielle).

        Pour les longs : max(break-even, trailing_stop).
        Le stop le plus haut est le plus protecteur.
        """
        be_stop = position.stop_loss      # = entry_price après break-even
        trailing = position.trailing_stop

        if be_stop is None and trailing is None:
            return None
        if be_stop is None:
            return trailing
        if trailing is None:
            return be_stop
        return max(be_stop, trailing)

"""
PaperRuntime — boucle de paper trading multi-actifs.

Fonctionnement :
  - Attend la fermeture de chaque bougie d'exécution (1h) + une marge de sécurité.
  - Pour chaque symbole activé :
      1. Gestion de la position ouverte sur la bougie qui vient de se fermer
         (stop-loss, prise partielle, break-even, trailing stop).
      2. Génération d'un signal via la stratégie (données 1h + filtre de tendance 4h).
      3. Validation par le RiskManager → ouverture d'une position simulée si signal valide.
  - Logs structurés à chaque étape. Aucun ordre réel n'est envoyé à l'exchange :
    les données de marché sont réelles (API publique Binance), les ordres sont
    simulés en mémoire.

Le paper trading partage la stratégie ET le RiskManager avec le backtest et le
mode live — ce qui garantit que ce qui est validé est ce qui tourne en production.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from binance.client import Client as BinanceClient

from config.settings import load_trading_config
from core.enums import OrderSide
from core.exceptions import RiskViolationError, StrategyError
from core.models import AccountSnapshot, Candle, Position, Signal
from data.downloader import BinanceDownloader
from data.fetcher import BinanceFetcher
from risk.manager import RiskManager
from strategy.base import StrategyContext
from strategy.v1_trend_following import TrendFollowingV1

import structlog

log = structlog.get_logger(__name__)

# Nombre de bougies à récupérer (assez pour faire converger EMA200 + marge)
_EXEC_CANDLES_TO_FETCH = 400   # 1h
_TREND_CANDLES_TO_FETCH = 300  # 4h
# Marge après la fermeture de la bougie (laisse Binance finaliser la kline)
_CLOSE_BUFFER_S = 90
_ATR_PERIOD = 14


@dataclass
class _PaperTrade:
    """État d'une position simulée, conservé d'un cycle à l'autre."""

    symbol: str
    entry_price: float
    quantity: float
    remaining_qty: float
    stop_loss: float
    take_profit_1r: float
    initial_risk: float
    atr: float

    partial_taken: bool = False
    break_even_set: bool = False
    trailing_active: bool = False
    trailing_stop: float | None = None

    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entry_reason: str = ""


class PaperRuntime:
    """
    Boucle de paper trading multi-actifs (long-only) pour TrendFollowingV1.

    Usage :
        runtime = PaperRuntime(symbols=["BTCUSDT", "ETHUSDT"], initial_equity=10_000.0)
        runtime.run()
    """

    def __init__(self, symbols: list[str], initial_equity: float = 10_000.0) -> None:
        self._symbols = symbols
        self._equity = initial_equity
        self._cfg = load_trading_config()
        self._risk_cfg = self._cfg.risk
        self._exec_tf = self._cfg.execution_timeframe
        self._trend_tf = self._cfg.trend_timeframe

        self._strategy = TrendFollowingV1()
        self._risk_manager = RiskManager()

        client: BinanceClient = BinanceDownloader.make_public_client()
        self._fetcher = BinanceFetcher(client)

        self._positions: dict[str, _PaperTrade] = {}
        self._closed_trades: list[dict[str, Any]] = []

        self._log = log.bind(component="PaperRuntime", strategy=self._strategy.name)
        self._log.info(
            "PaperRuntime initialized",
            symbols=symbols,
            equity=initial_equity,
            max_open_positions=self._risk_cfg.max_open_positions,
            risk_per_trade_pct=self._risk_cfg.risk_per_trade_pct,
        )

    # -----------------------------------------------------------------------
    # Boucle principale
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Boucle principale — tourne jusqu'à Ctrl+C."""
        _print_header(self._symbols, self._equity, self._risk_cfg, self._exec_tf)

        while True:
            try:
                sleep_s = _seconds_until_next_close(self._exec_tf)
                next_close = _next_close_utc(self._exec_tf)
                print(
                    f"\n[{_now_str()}] Prochain cycle {self._exec_tf} : "
                    f"{next_close.strftime('%Y-%m-%d %H:%M')} UTC "
                    f"(dans {sleep_s // 60} min {sleep_s % 60} s)"
                )
                time.sleep(sleep_s)
                self._run_cycle()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self._log.error("Unhandled error in paper loop", error=str(e), exc_info=True)
                print(f"\n[ERREUR] {e} — reprise dans 60 s")
                time.sleep(60)

    # -----------------------------------------------------------------------
    # Cycle
    # -----------------------------------------------------------------------

    def _run_cycle(self) -> None:
        ts = _now_str()
        print(f"\n{'='*64}")
        print(f"[{ts}] CYCLE — Équité : {self._equity:,.2f} USDT")
        print(f"  Positions ouvertes : {len(self._positions)}/{self._risk_cfg.max_open_positions}")
        for sym, pos in self._positions.items():
            try:
                cur = self._fetcher.get_latest_price(sym)
            except Exception:
                cur = pos.entry_price
            upnl = (cur - pos.entry_price) * pos.remaining_qty
            print(f"  • {sym:<12} entry={pos.entry_price:.4f}  SL={pos.stop_loss:.4f}  PnL≈{upnl:+.2f} USDT")
        print(f"{'='*64}")

        for symbol in self._symbols:
            try:
                self._process_symbol(symbol)
            except Exception as e:
                self._log.error("Symbol processing failed", symbol=symbol, error=str(e))
                print(f"  [{symbol}] ERREUR : {e}")

        self._print_cycle_summary()

    def _process_symbol(self, symbol: str) -> None:
        exec_candles = self._fetcher.get_candles(symbol, self._exec_tf, limit=_EXEC_CANDLES_TO_FETCH)
        trend_candles = self._fetcher.get_candles(symbol, self._trend_tf, limit=_TREND_CANDLES_TO_FETCH)
        if len(exec_candles) < 2 or len(trend_candles) < 2:
            self._log.warning("Not enough candles", symbol=symbol)
            return

        # Écarter la dernière bougie de chaque timeframe (potentiellement en formation)
        exec_closed = exec_candles[:-1]
        trend_closed = trend_candles[:-1]
        last_closed = exec_closed[-1]

        # 1. Gérer la position ouverte
        if symbol in self._positions:
            self._manage_position(symbol, last_closed, exec_closed)
            if symbol in self._positions:
                return  # déjà en position → pas d'entrée ce cycle

        # 2. Générer un signal
        signal = self._generate_signal(symbol, trend_closed, exec_closed)
        if signal.is_none:
            print(f"  [{symbol}] Pas de signal — {signal.reason}")
            return
        print(f"  [{symbol}] SIGNAL : {signal.signal_type.value} | {signal.reason}")

        # 3. Valider le risque et ouvrir
        self._try_open_position(signal)

    # -----------------------------------------------------------------------
    # Gestion des positions
    # -----------------------------------------------------------------------

    def _manage_position(self, symbol: str, candle: Candle, exec_closed: list[Candle]) -> None:
        pos = self._positions[symbol]
        low, high, close = candle.low, candle.high, candle.close

        # 1. Stop-loss
        if low <= pos.stop_loss:
            pnl = (pos.stop_loss - pos.entry_price) * pos.remaining_qty
            self._close_position(symbol, pos.stop_loss, pnl, reason="stop_loss")
            return

        # 2. Prise partielle à 1R
        if not pos.partial_taken and high >= pos.take_profit_1r:
            partial_qty = pos.remaining_qty * (self._risk_cfg.partial_take_pct / 100.0)
            pnl_partial = (pos.take_profit_1r - pos.entry_price) * partial_qty
            pos.remaining_qty -= partial_qty
            pos.partial_taken = True

            # Break-even
            pos.stop_loss = pos.entry_price
            pos.break_even_set = True

            # Activer le trailing
            atr = _compute_last_atr(exec_closed)
            pos.trailing_stop = (close - self._risk_cfg.trailing_atr_multiplier * atr) if atr else pos.entry_price
            pos.trailing_active = True

            self._equity += pnl_partial
            self._log.info(
                "Partial take", symbol=symbol, qty=round(partial_qty, 6),
                price=pos.take_profit_1r, pnl=round(pnl_partial, 2),
                remaining_qty=round(pos.remaining_qty, 6), new_sl=pos.stop_loss,
            )
            print(f"  [{symbol}] PARTIEL {pnl_partial:+.2f} USDT | SL → BE={pos.entry_price:.4f} | Trailing actif")

        # 3. Trailing stop touché
        if pos.trailing_active and pos.trailing_stop is not None:
            if low <= pos.trailing_stop:
                pnl = (pos.trailing_stop - pos.entry_price) * pos.remaining_qty
                self._close_position(symbol, pos.trailing_stop, pnl, reason="trailing_stop")
                return
            # 4. Mise à jour du trailing (ratchet — ne recule jamais)
            atr = _compute_last_atr(exec_closed)
            if atr:
                new_trailing = close - self._risk_cfg.trailing_atr_multiplier * atr
                if new_trailing > pos.trailing_stop:
                    pos.trailing_stop = new_trailing
                    self._log.debug("Trailing stop updated", symbol=symbol, trailing_stop=round(new_trailing, 4))

    def _close_position(self, symbol: str, exit_price: float, pnl: float, reason: str) -> None:
        pos = self._positions.pop(symbol)
        self._equity += pnl
        self._risk_manager.record_trade_closed(pnl)
        r_multiple = pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0

        self._closed_trades.append({
            "symbol": symbol,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "r_multiple": round(r_multiple, 2),
            "reason": reason,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "partial_taken": pos.partial_taken,
        })

        emoji = "✓" if pnl >= 0 else "✗"
        self._log.info(
            "Position closed", symbol=symbol, exit_price=exit_price,
            pnl=round(pnl, 2), r_multiple=round(r_multiple, 2),
            reason=reason, equity=round(self._equity, 2),
        )
        print(
            f"  [{symbol}] {emoji} FERMÉ ({reason.upper()}) "
            f"entry={pos.entry_price:.4f} → exit={exit_price:.4f} | "
            f"PnL={pnl:+.2f} USDT ({r_multiple:+.2f}R) | Équité={self._equity:,.2f}"
        )

    # -----------------------------------------------------------------------
    # Signal et ouverture
    # -----------------------------------------------------------------------

    def _generate_signal(
        self, symbol: str, trend_closed: list[Candle], exec_closed: list[Candle]
    ) -> Signal:
        context = StrategyContext(symbol=symbol, trend_candles=trend_closed, exec_candles=exec_closed)
        try:
            return self._strategy.generate_signal(context)
        except StrategyError as e:
            self._log.error("Strategy error", symbol=symbol, error=str(e))
            from core.enums import SignalType
            return Signal(
                signal_type=SignalType.NONE, symbol=symbol, timeframe=self._exec_tf,
                timestamp=datetime.now(timezone.utc), reason=f"StrategyError: {e}",
            )

    def _try_open_position(self, signal: Signal) -> None:
        account = self._build_account_snapshot()
        try:
            order_req = self._risk_manager.validate_signal(signal, account)
        except RiskViolationError as e:
            self._log.info("Signal refused by risk manager", symbol=signal.symbol, rule=e.rule, reason=str(e))
            print(f"  [{signal.symbol}] REFUSÉ ({e.rule}) — {e}")
            return

        entry_price = signal.close_price
        qty = order_req.quantity
        sl = order_req.stop_loss
        take_profit_1r = order_req.take_profit
        initial_risk = (entry_price - sl) * qty if sl else 0.0

        if qty <= 0:
            print(f"  [{signal.symbol}] REFUSÉ (qty=0 — ATR trop faible)")
            return

        self._positions[signal.symbol] = _PaperTrade(
            symbol=signal.symbol,
            entry_price=entry_price,
            quantity=qty,
            remaining_qty=qty,
            stop_loss=sl,
            take_profit_1r=take_profit_1r,
            initial_risk=initial_risk,
            atr=signal.atr,
            entry_reason=signal.reason,
        )
        self._log.info(
            "Position opened", symbol=signal.symbol, entry=entry_price,
            qty=round(qty, 6), stop_loss=round(sl, 4),
            take_profit_1r=round(take_profit_1r, 4), initial_risk=round(initial_risk, 2),
            equity=round(self._equity, 2),
        )
        print(
            f"  [{signal.symbol}] OUVERT entry={entry_price:.4f} | SL={sl:.4f} | "
            f"TP1R={take_profit_1r:.4f} | Risque={initial_risk:.2f} USDT"
        )

    def _build_account_snapshot(self) -> AccountSnapshot:
        positions = []
        for sym, trade in self._positions.items():
            try:
                current_price = self._fetcher.get_latest_price(sym)
            except Exception:
                current_price = trade.entry_price
            positions.append(Position(
                symbol=sym,
                side=OrderSide.BUY,
                quantity=trade.remaining_qty,
                entry_price=trade.entry_price,
                current_price=current_price,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit_1r,
                partial_taken=trade.partial_taken,
                break_even_set=trade.break_even_set,
                initial_stop=trade.stop_loss,
                take_profit_1r=trade.take_profit_1r,
                initial_quantity=trade.quantity,
                initial_risk=trade.initial_risk,
                trailing_active=trade.trailing_active,
            ))
        total_exposure = sum(t.remaining_qty * t.entry_price for t in self._positions.values())
        return AccountSnapshot(
            timestamp=datetime.now(timezone.utc),
            total_equity=self._equity,
            available_balance=max(0.0, self._equity - total_exposure),
            open_positions=positions,
        )

    def _print_cycle_summary(self) -> None:
        realized_pnl = sum(t["pnl"] for t in self._closed_trades)
        print(
            f"\n  Résumé — Équité : {self._equity:,.2f} USDT | "
            f"Positions : {len(self._positions)} | "
            f"Trades fermés : {len(self._closed_trades)} | "
            f"PnL réalisé : {realized_pnl:+.2f} USDT"
        )


# ---------------------------------------------------------------------------
# Helpers timing / ATR / affichage
# ---------------------------------------------------------------------------

_TF_HOURS = {"1h": 1, "2h": 2, "4h": 4, "6h": 6, "12h": 12, "1d": 24}


def _next_close_utc(timeframe: str) -> datetime:
    """Prochain timestamp de fermeture de bougie pour le timeframe donné (UTC)."""
    step = _TF_HOURS.get(timeframe, 1)
    now = datetime.now(timezone.utc)
    next_hour = ((now.hour // step) + 1) * step
    base = now.replace(minute=_CLOSE_BUFFER_S // 60, second=_CLOSE_BUFFER_S % 60, microsecond=0)
    if next_hour >= 24:
        return base.replace(hour=0) + timedelta(days=1)
    return base.replace(hour=next_hour)


def _seconds_until_next_close(timeframe: str) -> int:
    delta = (_next_close_utc(timeframe) - datetime.now(timezone.utc)).total_seconds()
    return max(int(delta), 10)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _compute_last_atr(candles: list[Candle], period: int = _ATR_PERIOD) -> float | None:
    """ATR (moyenne simple des True Ranges) sur les dernières bougies fermées."""
    if len(candles) < period + 1:
        return None
    recent = candles[-(period + 1):]
    tr_values = []
    for i in range(1, len(recent)):
        prev_close = recent[i - 1].close
        tr = max(
            recent[i].high - recent[i].low,
            abs(recent[i].high - prev_close),
            abs(recent[i].low - prev_close),
        )
        tr_values.append(tr)
    return sum(tr_values[-period:]) / period if tr_values else None


def _print_header(symbols: list[str], equity: float, risk_cfg: Any, exec_tf: str) -> None:
    print("\n" + "=" * 64)
    print("  PAPER TRADING — TrendFollowingV1")
    print("=" * 64)
    print(f"  Symboles actifs   : {', '.join(symbols)}")
    print(f"  Capital initial   : {equity:,.2f} USDT")
    print(f"  Timeframe exéc.   : {exec_tf}")
    print(f"  Risque/trade      : {risk_cfg.risk_per_trade_pct}%")
    print(f"  Max positions     : {risk_cfg.max_open_positions}")
    print(f"  Stop ATR mult     : {risk_cfg.stop_atr_multiplier}x")
    print(f"  Prise partielle   : {risk_cfg.partial_take_at_r}R ({risk_cfg.partial_take_pct:.0f}%)")
    print(f"  Trailing stop     : {risk_cfg.trailing_atr_multiplier}x ATR")
    print("=" * 64)
    print("  En attente de la prochaine bougie fermée... (Ctrl+C pour arrêter)\n")

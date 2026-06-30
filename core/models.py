"""
Modèles de données centraux du bot.

Ces objets sont les "langages communs" entre tous les modules.
Strategy, RiskManager, Executor, Portfolio parlent tous via ces types.

Principes :
- Immutabilité privilégiée (frozen=True où ça a du sens)
- Pydantic pour validation + sérialisation vers/depuis DB
- Pas de logique métier dans les modèles (juste données + propriétés simples)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    SetupType,
    SignalType,
)


# ---------------------------------------------------------------------------
# Candle — donnée de base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    """
    Une bougie OHLCV.

    Frozen dataclass : immutable, hashable, utilisable comme clé de dict/set.
    timestamp en UTC (toujours).
    """

    symbol: str
    timeframe: str
    timestamp: datetime  # UTC, ouverture de la bougie
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def range_size(self) -> float:
        return self.high - self.low

    def __repr__(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M")
        return f"Candle({self.symbol} {self.timeframe} {ts} C={self.close:.4f})"


# ---------------------------------------------------------------------------
# Signal — sortie de la stratégie
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """
    Signal produit par la stratégie.

    La stratégie ne décide pas de la taille de position ni de l'exécution.
    Elle indique seulement : quoi faire, sur quel actif, avec quelles références.
    """

    signal_type: SignalType
    symbol: str
    timeframe: str
    timestamp: datetime

    # Contexte technique au moment du signal (pour sizing et SL)
    close_price: float = 0.0
    atr: float = 0.0           # ATR courant (utilisé pour SL et trailing)
    setup_type: SetupType | None = None

    # Infos additionnelles utiles pour le risk manager
    reason: str = ""           # Description lisible pour logs/notifs

    # Override optionnel : si > 0, utilisé comme TP exact (ex: mean reversion → EMA20)
    take_profit_price: float = 0.0
    # Si True, sortie totale au TP (no partial, no break-even, no trailing)
    full_exit_at_tp: bool = False

    # Valeurs d'indicateurs et résultats de filtres tels qu'utilisés pour la décision.
    # Renseigné par la stratégie — source de vérité pour les logs de cycle.
    diagnostics: dict = field(default_factory=dict)

    @property
    def is_entry(self) -> bool:
        return self.signal_type in (
            SignalType.BUY_BREAKOUT,
            SignalType.BUY_PULLBACK,
            SignalType.SELL_SHORT_PULLBACK,
            SignalType.SELL_BREAKOUT,
        )

    @property
    def is_long(self) -> bool:
        return self.signal_type in (SignalType.BUY_BREAKOUT, SignalType.BUY_PULLBACK)

    @property
    def is_short(self) -> bool:
        return self.signal_type in (SignalType.SELL_SHORT_PULLBACK, SignalType.SELL_BREAKOUT)

    @property
    def is_exit(self) -> bool:
        return self.signal_type in (SignalType.CLOSE_PARTIAL, SignalType.CLOSE_ALL)

    @property
    def is_none(self) -> bool:
        return self.signal_type == SignalType.NONE

    def __repr__(self) -> str:
        return f"Signal({self.signal_type} {self.symbol} @ {self.close_price:.4f})"


# ---------------------------------------------------------------------------
# OrderRequest — intention d'ordre (avant envoi à l'exchange)
# ---------------------------------------------------------------------------


@dataclass
class OrderRequest:
    """
    Demande d'ordre produite par le RiskManager.

    Représente ce qu'on VEUT faire. L'Executor se charge de l'envoyer.
    Séparer OrderRequest de Order permet de valider sans exécuter.
    """

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float            # En unité de base (ex: BTC)
    price: float | None = None # Pour les ordres limit

    # Niveaux calculés par le risk manager
    stop_loss: float | None = None
    take_profit: float | None = None

    # Contexte (traçabilité)
    signal: Signal | None = None
    client_order_id: str = ""  # ID interne pour suivi
    # Comportement de sortie (propagé depuis Signal)
    full_exit_at_tp: bool = False  # Si True, sortie totale au TP sans partial ni trailing

    def __repr__(self) -> str:
        return (
            f"OrderRequest({self.side} {self.quantity:.6f} {self.symbol} "
            f"@ {self.price or 'MARKET'} SL={self.stop_loss})"
        )


# ---------------------------------------------------------------------------
# Order — ordre confirmé par l'exchange (ou simulé en paper/backtest)
# ---------------------------------------------------------------------------


class Order(BaseModel):
    """
    Ordre tel que retourné/confirmé par l'exchange.

    Pydantic pour faciliter la sérialisation vers la DB.
    """

    id: str                              # ID exchange (ou UUID en paper/backtest)
    client_order_id: str = ""
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    quantity: float
    filled_quantity: float = 0.0
    price: float | None = None           # Prix limite
    avg_fill_price: float | None = None  # Prix moyen d'exécution
    commission: float = 0.0
    commission_asset: str = "USDT"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    @property
    def fill_value(self) -> float:
        """Valeur totale exécutée (hors commission)."""
        if self.avg_fill_price is None:
            return 0.0
        return self.filled_quantity * self.avg_fill_price


# ---------------------------------------------------------------------------
# Trade — trade complet (entrée + sortie)
# ---------------------------------------------------------------------------


class Trade(BaseModel):
    """
    Trade complet : une entrée et une (ou plusieurs) sorties.

    Un trade regroupe les ordres d'entrée et de sortie associés
    pour calculer le PnL réalisé et conserver l'historique.
    """

    id: str
    symbol: str
    side: OrderSide
    setup_type: SetupType | None = None

    # Entrée
    entry_order_id: str = ""
    entry_price: float = 0.0
    entry_quantity: float = 0.0
    entry_time: datetime = Field(default_factory=datetime.utcnow)

    # Sortie (peut être partielle)
    exit_orders: list[str] = Field(default_factory=list)
    exit_price: float | None = None      # Prix moyen de sortie
    exit_quantity: float = 0.0
    exit_time: datetime | None = None

    # Niveaux
    stop_loss: float | None = None
    take_profit: float | None = None
    initial_risk: float = 0.0           # $ risqués sur ce trade

    # Résultats
    realized_pnl: float = 0.0
    commission_total: float = 0.0
    status: PositionStatus = PositionStatus.OPEN

    # Suivi enrichi (rempli par PortfolioManager)
    take_profit_1r: float | None = None    # Niveau de prise partielle 1R
    exit_reason: str = ""                   # Raison de sortie (stop_loss, trailing, etc.)
    partial_taken: bool = False             # True si une prise partielle a eu lieu

    @property
    def net_pnl(self) -> float:
        return self.realized_pnl - self.commission_total

    @property
    def r_multiple(self) -> float | None:
        """PnL exprimé en multiples de R (risque initial)."""
        if self.initial_risk == 0:
            return None
        return self.net_pnl / self.initial_risk


# ---------------------------------------------------------------------------
# Position — position ouverte (état courant)
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """
    Position actuellement ouverte sur un actif.

    Mise à jour en temps réel par le portfolio manager.
    Distincte de Trade : une position est l'état courant, un trade est l'historique.
    """

    symbol: str
    side: OrderSide
    quantity: float              # Quantité restante (après prises partielles)
    entry_price: float
    current_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop: float | None = None
    opened_at: datetime = field(default_factory=datetime.utcnow)
    trade_id: str = ""           # Référence au Trade associé

    # Flags de gestion
    partial_taken: bool = False   # Prise partielle déjà effectuée
    break_even_set: bool = False  # Break-even déjà déplacé

    # Gestion avancée (backtest engine)
    initial_stop: float | None = None      # SL original du signal (référence calcul 1R)
    take_profit_1r: float | None = None    # Niveau 1R pour prise partielle 50%
    initial_quantity: float = 0.0          # Quantité avant toute prise partielle
    initial_risk: float = 0.0             # $ risqués = (entry - initial_stop) × qty_initiale
    trailing_active: bool = False          # True après prise partielle à 1R
    exit_reason: str = ""                  # Raison de sortie (log/analyse)
    full_exit_at_tp: bool = False          # Si True, sortie totale au TP (no partial/trailing)

    @property
    def unrealized_pnl(self) -> float:
        if self.side == OrderSide.BUY:
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def market_value(self) -> float:
        return self.current_price * self.quantity

    def __repr__(self) -> str:
        return (
            f"Position({self.symbol} {self.side} qty={self.quantity:.6f} "
            f"entry={self.entry_price:.4f} pnl={self.unrealized_pnl:.2f})"
        )


# ---------------------------------------------------------------------------
# AccountSnapshot — état du compte à un instant T
# ---------------------------------------------------------------------------


@dataclass
class AccountSnapshot:
    """
    Snapshot du compte (balance, exposition) utilisé par le risk manager.

    Alimenté par l'exchange en live, simulé en paper/backtest.
    """

    timestamp: datetime
    total_equity: float          # Capital total (disponible + engagé)
    available_balance: float     # Liquide disponible pour ouvrir de nouveaux trades
    open_positions: list[Position] = field(default_factory=list)

    @property
    def total_exposure(self) -> float:
        """Valeur totale des positions ouvertes."""
        return sum(p.market_value for p in self.open_positions)

    @property
    def exposure_pct(self) -> float:
        """% du capital engagé."""
        if self.total_equity == 0:
            return 0.0
        return (self.total_exposure / self.total_equity) * 100

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.open_positions)

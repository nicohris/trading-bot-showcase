"""
Enumerations partagées dans tout le projet.

Centraliser les enums évite les strings magiques dispersés dans le code.
"""

from enum import Enum, auto


class TradingMode(str, Enum):
    """Mode d'exécution du bot."""
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class OrderSide(str, Enum):
    """Côté d'un ordre."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Type d'ordre envoyé à l'exchange."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"


class OrderStatus(str, Enum):
    """Statut d'un ordre."""
    PENDING = "PENDING"      # Pas encore envoyé à l'exchange
    OPEN = "OPEN"            # Envoyé, non rempli
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SignalType(str, Enum):
    """Type de signal produit par la stratégie."""
    NONE = "NONE"
    BUY_BREAKOUT = "BUY_BREAKOUT"              # Long — setup breakout
    BUY_PULLBACK = "BUY_PULLBACK"              # Long — setup pullback
    SELL_SHORT_PULLBACK = "SELL_SHORT_PULLBACK" # Short — retest baissier EMA
    SELL_BREAKOUT = "SELL_BREAKOUT"             # Short — cassure baissière (Stratégie C)
    CLOSE_PARTIAL = "CLOSE_PARTIAL"             # Prise partielle
    CLOSE_ALL = "CLOSE_ALL"                     # Fermeture complète


class TimeFrame(str, Enum):
    """Timeframes supportés (notation Binance)."""
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class SetupType(str, Enum):
    """Type de setup d'entrée (sous-catégorie de signal)."""
    BREAKOUT = "BREAKOUT"
    PULLBACK_EMA20 = "PULLBACK_EMA20"
    PULLBACK_EMA50 = "PULLBACK_EMA50"
    SHORT_RETEST_EMA20 = "SHORT_RETEST_EMA20"   # Retest EMA20 en tendance baissière
    SHORT_RETEST_EMA50 = "SHORT_RETEST_EMA50"   # Retest EMA50 en tendance baissière
    MEAN_REVERSION = "MEAN_REVERSION"
    VOLATILITY_BREAKOUT = "VOLATILITY_BREAKOUT"


class PositionStatus(str, Enum):
    """Statut d'une position."""
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"

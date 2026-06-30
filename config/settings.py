"""
Configuration centrale du bot.

Deux niveaux :
- Settings      : variables d'environnement (.env) — credentials, mode, infra
- TradingConfig : paramètres trading (trading_config.yaml) — stratégie, risque, marchés

Séparation intentionnelle : les credentials ne se mélangent pas aux paramètres trading.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_TRADING_CONFIG = CONFIG_DIR / "trading_config.yaml"


# ---------------------------------------------------------------------------
# Settings (depuis .env)
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Variables d'infrastructure chargées depuis .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Mode d'exécution
    trading_mode: str = Field(default="paper", description="backtest | paper | live")

    # Binance
    binance_api_key: str = Field(default="", description="Binance API key")
    binance_api_secret: str = Field(default="", description="Binance API secret")
    binance_testnet: bool = Field(default=False, description="Use Binance testnet")

    # Telegram
    telegram_bot_token: str = Field(default="", description="Telegram bot token")
    telegram_chat_id: str = Field(default="", description="Telegram chat ID")

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json", description="json | console")

    # Storage
    database_url: str = Field(default="sqlite:///./data/trading_bot.db")

    # Runtime
    polling_interval_seconds: int = Field(default=60)

    @field_validator("trading_mode")
    @classmethod
    def validate_trading_mode(cls, v: str) -> str:
        allowed = {"backtest", "paper", "live"}
        if v not in allowed:
            raise ValueError(f"trading_mode must be one of {allowed}, got '{v}'")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v_upper

    def is_live(self) -> bool:
        return self.trading_mode == "live"

    def is_paper(self) -> bool:
        return self.trading_mode == "paper"

    def is_backtest(self) -> bool:
        return self.trading_mode == "backtest"

    def require_binance_credentials(self) -> None:
        """Lève une exception si les credentials Binance manquent (requis en live)."""
        if not self.binance_api_key or not self.binance_api_secret:
            raise ValueError(
                "BINANCE_API_KEY and BINANCE_API_SECRET are required in live/paper mode"
            )


# ---------------------------------------------------------------------------
# TradingConfig (depuis trading_config.yaml)
# ---------------------------------------------------------------------------


class MarketConfig:
    """Configuration d'un marché tradé."""

    def __init__(self, symbol: str, base: str, quote: str, enabled: bool = True) -> None:
        self.symbol = symbol
        self.base = base
        self.quote = quote
        self.enabled = enabled

    def __repr__(self) -> str:
        return f"MarketConfig(symbol={self.symbol}, enabled={self.enabled})"


class StrategyConfig:
    """Paramètres numériques de la stratégie de référence (TrendFollowingV1)."""

    def __init__(self, data: dict) -> None:
        self.name: str = data["name"]
        self.ema_fast: int = data["ema_fast"]
        self.ema_slow: int = data["ema_slow"]
        self.ema_pullback: int = data["ema_pullback"]
        self.atr_period: int = data["atr_period"]
        self.atr_ma_period: int = data["atr_ma_period"]
        self.volume_ma_period: int = data["volume_ma_period"]
        self.breakout_lookback: int = data["breakout_lookback"]
        # Tolérance de proximité pour le setup pullback, en multiples d'ATR.
        # Un close à moins de (pullback_proximity_atr × ATR) d'une EMA est "proche".
        self.pullback_proximity_atr: float = data.get("pullback_proximity_atr", 0.5)


class RiskConfig:
    """Paramètres de gestion du risque."""

    def __init__(self, data: dict) -> None:
        self.risk_per_trade_pct: float = data["risk_per_trade_pct"]
        self.max_open_positions: int = int(data.get("max_open_positions", 4))
        self.max_total_exposure_pct: float = data["max_total_exposure_pct"]
        self.max_positions_per_symbol: int = data["max_positions_per_symbol"]
        self.stop_atr_multiplier: float = data["stop_atr_multiplier"]
        self.partial_take_at_r: float = data["partial_take_at_r"]
        self.partial_take_pct: float = data["partial_take_pct"]
        self.break_even_at_r: float = data["break_even_at_r"]
        self.trailing_atr_multiplier: float = data["trailing_atr_multiplier"]
        self.max_daily_loss_pct: float = data["max_daily_loss_pct"]
        self.max_consecutive_losses: int = data["max_consecutive_losses"]


class BacktestConfig:
    """Paramètres spécifiques au backtest."""

    def __init__(self, data: dict) -> None:
        self.initial_capital: float = data["initial_capital"]
        self.fee_rate: float = data["fee_rate"]
        self.slippage_pct: float = data["slippage_pct"]


class NotificationsConfig:
    """Quels événements déclenchent une notification."""

    def __init__(self, data: dict) -> None:
        self.on_trade_open: bool = data.get("on_trade_open", True)
        self.on_trade_close: bool = data.get("on_trade_close", True)
        self.on_daily_summary: bool = data.get("on_daily_summary", True)
        self.on_risk_breach: bool = data.get("on_risk_breach", True)
        self.on_error: bool = data.get("on_error", True)


class TradingConfig:
    """Configuration trading complète parsée depuis trading_config.yaml."""

    def __init__(self, path: Path = DEFAULT_TRADING_CONFIG) -> None:
        with open(path) as f:
            raw = yaml.safe_load(f)

        self.markets: list[MarketConfig] = [MarketConfig(**m) for m in raw["markets"]]
        self.timeframes: dict[str, str] = raw["timeframes"]
        self.strategy = StrategyConfig(raw["strategy"])
        self.risk = RiskConfig(raw["risk"])
        self.backtest = BacktestConfig(raw["backtest"])
        self.notifications = NotificationsConfig(raw.get("notifications", {}))

    @property
    def trend_timeframe(self) -> str:
        return self.timeframes["trend"]

    @property
    def execution_timeframe(self) -> str:
        return self.timeframes["execution"]

    @property
    def symbols(self) -> list[str]:
        """Retourne uniquement les symboles des marchés activés."""
        return [m.symbol for m in self.markets if m.enabled]


# ---------------------------------------------------------------------------
# Accesseurs globaux (singleton via lru_cache)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    """Charge les settings depuis .env. Singleton."""
    return Settings()


@lru_cache(maxsize=1)
def load_trading_config() -> TradingConfig:
    """Charge la config trading depuis YAML. Singleton."""
    return TradingConfig()

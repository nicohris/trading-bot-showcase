"""
Persistance SQLite pour les trades, runs et événements.

SQLite est suffisant pour V1 (petit serveur, usage single-process).
SQLAlchemy 2.0 en mode synchrone pour garder le code simple.

Migration vers PostgreSQL possible plus tard sans changer l'interface.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.exceptions import StorageError
from core.models import Trade

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Modèles SQLAlchemy (schéma DB)
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    """Représentation persistée d'un Trade."""
    __tablename__ = "trades"

    id = Column(String, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    setup_type = Column(String, nullable=True)
    entry_price = Column(Float, nullable=False)
    entry_quantity = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    exit_price = Column(Float, nullable=True)
    exit_quantity = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    realized_pnl = Column(Float, default=0.0)
    commission_total = Column(Float, default=0.0)
    status = Column(String, nullable=False)
    r_multiple = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BotRunRecord(Base):
    """Enregistrement d'un run du bot (démarrage/arrêt)."""
    __tablename__ = "bot_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mode = Column(String, nullable=False)  # backtest | paper | live
    started_at = Column(DateTime, nullable=False)
    stopped_at = Column(DateTime, nullable=True)
    symbols = Column(String, nullable=False)  # JSON list
    final_equity = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)


class EventRecord(Base):
    """Log des événements importants (pour audit)."""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    event_type = Column(String, nullable=False)  # trade_opened, risk_violation, etc.
    symbol = Column(String, nullable=True)
    data = Column(Text, nullable=True)  # JSON


# ---------------------------------------------------------------------------
# Database — interface principale
# ---------------------------------------------------------------------------


class Database:
    """
    Interface de persistance SQLite.

    Utilisation :
        db = Database("sqlite:///./data/trading_bot.db")
        db.save_trade(trade)
        trades = db.get_trades(symbol="BTCUSDT")
    """

    def __init__(self, database_url: str = "sqlite:///./data/trading_bot.db") -> None:
        # Créer le dossier data si nécessaire
        if database_url.startswith("sqlite:///"):
            db_path = Path(database_url.replace("sqlite:///", ""))
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self._engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},  # SQLite only
            echo=False,
        )
        self._Session = sessionmaker(bind=self._engine)
        self._log = log.bind(component="Database")

    def init_db(self) -> None:
        """Crée les tables si elles n'existent pas."""
        Base.metadata.create_all(self._engine)
        self._log.info("Database initialized")

    def save_trade(self, trade: Trade) -> None:
        """Persiste un Trade (insert ou update)."""
        try:
            with self._Session() as session:
                record = session.get(TradeRecord, trade.id)
                if record is None:
                    record = TradeRecord(id=trade.id)
                    session.add(record)

                record.symbol = trade.symbol
                record.side = trade.side.value
                record.setup_type = trade.setup_type.value if trade.setup_type else None
                record.entry_price = trade.entry_price
                record.entry_quantity = trade.entry_quantity
                record.entry_time = trade.entry_time
                record.exit_price = trade.exit_price
                record.exit_quantity = trade.exit_quantity
                record.exit_time = trade.exit_time
                record.stop_loss = trade.stop_loss
                record.take_profit = trade.take_profit
                record.realized_pnl = trade.realized_pnl
                record.commission_total = trade.commission_total
                record.status = trade.status.value
                record.r_multiple = trade.r_multiple

                session.commit()
        except Exception as e:
            raise StorageError(f"Failed to save trade {trade.id}: {e}") from e

    def get_trades(
        self,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[TradeRecord]:
        """Récupère les trades depuis la DB."""
        try:
            with self._Session() as session:
                query = session.query(TradeRecord).order_by(TradeRecord.entry_time.desc())
                if symbol:
                    query = query.filter(TradeRecord.symbol == symbol)
                return query.limit(limit).all()
        except Exception as e:
            raise StorageError(f"Failed to get trades: {e}") from e

    def log_event(self, event_type: str, symbol: str | None = None, data: dict | None = None) -> None:
        """Enregistre un événement dans la table events."""
        try:
            with self._Session() as session:
                record = EventRecord(
                    timestamp=datetime.utcnow(),
                    event_type=event_type,
                    symbol=symbol,
                    data=json.dumps(data) if data else None,
                )
                session.add(record)
                session.commit()
        except Exception as e:
            # Les erreurs de log d'événement ne doivent pas crasher le bot
            self._log.warning("Failed to log event", event_type=event_type, error=str(e))

    def start_run(self, mode: str, symbols: list[str]) -> int:
        """Enregistre le démarrage d'un run. Retourne l'ID du run."""
        try:
            with self._Session() as session:
                record = BotRunRecord(
                    mode=mode,
                    started_at=datetime.utcnow(),
                    symbols=json.dumps(symbols),
                )
                session.add(record)
                session.commit()
                return record.id
        except Exception as e:
            raise StorageError(f"Failed to start run record: {e}") from e

    def stop_run(self, run_id: int, final_equity: float | None = None) -> None:
        """Enregistre l'arrêt d'un run."""
        try:
            with self._Session() as session:
                record = session.get(BotRunRecord, run_id)
                if record:
                    record.stopped_at = datetime.utcnow()
                    record.final_equity = final_equity
                    session.commit()
        except Exception as e:
            self._log.warning("Failed to stop run record", run_id=run_id, error=str(e))

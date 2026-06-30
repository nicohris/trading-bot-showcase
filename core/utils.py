"""
Utilitaires partagés — uniquement des fonctions pures sans effets de bord.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Retourne l'heure UTC courante avec timezone info."""
    return datetime.now(timezone.utc)


def generate_id(prefix: str = "") -> str:
    """Génère un ID unique court (8 chars hex)."""
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}_{uid}" if prefix else uid


def round_to_precision(value: float, precision: int) -> float:
    """Arrondit une valeur à N décimales (pour respecter les step sizes exchange)."""
    return round(value, precision)


def pct_change(old: float, new: float) -> float:
    """Calcule le % de changement entre old et new."""
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Borne une valeur entre min et max."""
    return max(min_val, min(max_val, value))


def timeframe_to_seconds(timeframe: str) -> int:
    """Convertit un timeframe Binance ('1h', '4h', '1d') en secondes."""
    mapping = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
        "1w": 604800,
    }
    if timeframe not in mapping:
        raise ValueError(f"Timeframe inconnu: {timeframe}")
    return mapping[timeframe]

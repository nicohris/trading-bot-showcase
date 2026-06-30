"""
Calcul des indicateurs techniques.

Fonctions pures : reçoivent un DataFrame, retournent un DataFrame enrichi.
Pas d'effets de bord, pas de dépendances externes au module.

Calculs purs pandas/numpy (sans librairie TA externe).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_ema(df: pd.DataFrame, period: int, column: str = "close") -> pd.DataFrame:
    """Ajoute une EMA au DataFrame. Colonne résultante : ema_{period}"""
    df = df.copy()
    df[f"ema_{period}"] = df[column].ewm(span=period, adjust=False).mean()
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Ajoute l'ATR au DataFrame. Colonne résultante : atr_{period}"""
    df = df.copy()
    close_prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - close_prev).abs(),
            (df["low"] - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df[f"atr_{period}"] = tr.ewm(span=period, adjust=False).mean()
    return df


def add_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Ajoute la moyenne mobile du volume. Colonne : volume_ma_{period}"""
    df = df.copy()
    df[f"volume_ma_{period}"] = df["volume"].rolling(period).mean()
    return df


def add_rolling_high(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Ajoute le plus haut des N dernières bougies (excluant la bougie courante).

    Utilisé pour le setup breakout : clôture > high des N bougies précédentes.
    Le shift(1) exclut la bougie courante du calcul.
    """
    df = df.copy()
    df[f"rolling_high_{period}"] = df["high"].shift(1).rolling(period).max()
    return df


def add_rolling_low(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Ajoute le plus bas des N dernières bougies (excluant la bougie courante).

    Le shift(1) exclut la bougie courante du calcul.
    """
    df = df.copy()
    df[f"rolling_low_{period}"] = df["low"].shift(1).rolling(period).min()
    return df


def compute_ema_slope(df: pd.DataFrame, ema_col: str, periods: int = 3) -> pd.Series:
    """
    Calcule la pente d'une EMA sur N périodes.

    Retourne une Series : positif = tendance haussière, négatif = baissière.
    Méthode simple : différence entre la valeur actuelle et N périodes avant.
    """
    return df[ema_col] - df[ema_col].shift(periods)


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Ajoute l'ADX (Average Directional Index) au DataFrame.

    Colonne résultante : adx_{period}. Indicateur de force de tendance.
    """
    df = df.copy()
    high = df["high"]
    low = df["low"]
    close = df["close"]

    close_prev = close.shift(1)
    high_prev = high.shift(1)
    low_prev = low.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high - high_prev
    down_move = low_prev - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=df.index).ewm(span=period, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean()
    atr_s = tr.ewm(span=period, adjust=False).mean()

    plus_di = 100 * plus_dm_s / atr_s
    minus_di = 100 * minus_dm_s / atr_s

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(span=period, adjust=False).mean()

    df[f"adx_{period}"] = adx
    return df


def prepare_trend_indicators(
    df: pd.DataFrame,
    ema_fast: int = 50,
    ema_slow: int = 200,
) -> pd.DataFrame:
    """
    Ajoute les indicateurs nécessaires au filtre de tendance (timeframe lent, ex: 4h).

    Retourne le DataFrame enrichi avec :
    - ema_{ema_fast}, ema_{ema_slow}
    - ema_{ema_fast}_slope (sur 3 périodes)
    """
    df = add_ema(df, ema_fast)
    df = add_ema(df, ema_slow)
    ema_col = f"ema_{ema_fast}"
    df[f"{ema_col}_slope"] = compute_ema_slope(df, ema_col)
    return df


def prepare_execution_indicators(
    df: pd.DataFrame,
    ema_fast: int = 50,
    ema_slow: int = 200,
    ema_pullback: int = 20,
    atr_period: int = 14,
    atr_ma_period: int = 20,
    volume_ma_period: int = 20,
    breakout_lookback: int = 20,
) -> pd.DataFrame:
    """
    Ajoute les indicateurs nécessaires aux signaux d'exécution (timeframe rapide, ex: 1h).

    Retourne le DataFrame enrichi avec :
    - ema_{ema_pullback}, ema_{ema_fast}, ema_{ema_slow}
    - atr_{atr_period}, atr_ma_{atr_ma_period}
    - volume_ma_{volume_ma_period}
    - rolling_high_{breakout_lookback}
    """
    df = add_ema(df, ema_fast)
    df = add_ema(df, ema_slow)
    df = add_ema(df, ema_pullback)
    df = add_atr(df, atr_period)
    df[f"atr_ma_{atr_ma_period}"] = df[f"atr_{atr_period}"].rolling(atr_ma_period).mean()
    df = add_volume_ma(df, volume_ma_period)
    df = add_rolling_high(df, breakout_lookback)
    return df

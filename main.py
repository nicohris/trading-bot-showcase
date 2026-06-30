"""
Point d'entrée principal du bot.

Utilisation :
    python main.py --help
    python main.py check
    python main.py backtest --symbols BTCUSDT --start 2024-01-01
    python main.py backtest --symbols BTCUSDT,ETHUSDT --start 2023-01-01 --end 2024-12-31 --verbose
    python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01
    python main.py data info
    python main.py paper
    python main.py live
"""

from __future__ import annotations

import io
import sys

# Force UTF-8 output sur Windows (évite les erreurs d'encodage avec les symboles Unicode)
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from cli.commands import cli

if __name__ == "__main__":
    cli()

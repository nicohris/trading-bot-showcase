"""
Configuration du logging structuré avec structlog.

Principe : un seul point d'initialisation, appelé au démarrage du bot.
Partout dans le code : `import structlog; log = structlog.get_logger(__name__)`

Le format JSON facilite l'ingestion par des outils de log (Loki, Datadog, etc.)
et permet des recherches propres même avec un simple `grep`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    log_file: str | None = None,
) -> None:
    """
    Configure structlog pour tout le projet.

    Args:
        log_level: DEBUG | INFO | WARNING | ERROR | CRITICAL
        log_format: 'json' pour production, 'console' pour développement local
        log_file: Chemin vers un fichier de log (optionnel).
                  Si fourni, les logs sont écrits dans le fichier EN PLUS de stdout.
    """

    # Processors communs (enrichissement des logs)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "console":
        # Format lisible pour développement local
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    else:
        # Format JSON pour production
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # False pour permettre la reconfiguration
    )

    # --- Logging stdlib (libs tierces : binance, etc.) ---
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Supprimer les handlers existants avant d'en ajouter de nouveaux
    root_logger.handlers.clear()

    if log_file:
        # Mode fichier (daemon/production) : fichier uniquement.
        # Pas de console_handler pour éviter la double écriture si systemd
        # capture stdout vers le même fichier.
        _add_file_handler(root_logger, log_file, log_level)
    else:
        # Mode interactif : console uniquement
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(console_handler)


def _add_file_handler(root_logger: logging.Logger, log_file: str, log_level: str) -> None:
    """Ajoute un handler de fichier au logger stdlib."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(getattr(logging, log_level.upper()))
    # Le fichier reçoit le texte brut (identique à stdout)
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)


def get_log_file_path(run_type: str = "backtest") -> Path:
    """
    Retourne le chemin du fichier de log pour un run donné.

    Crée le dossier logs/ si nécessaire.
    """
    from datetime import datetime
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"{run_type}_{timestamp}.log"

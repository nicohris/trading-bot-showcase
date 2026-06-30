"""
Validation de l'intégrité des données OHLCV.

Design :
- Pas de crash sur anomalie — on avertit et on laisse le code appelant décider.
- ValidationResult agrège tous les problèmes détectés.
- Les règles sont indépendantes les unes des autres.
- Les trous détectés sont listés précisément pour investigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog

from core.exceptions import DataError
from core.models import Candle
from core.utils import timeframe_to_seconds

log = structlog.get_logger(__name__)


@dataclass
class ValidationIssue:
    """Un problème détecté lors de la validation."""
    level: str          # "error" | "warning"
    rule: str           # identifiant de la règle
    message: str
    candle_index: int | None = None
    timestamp: datetime | None = None


@dataclass
class ValidationResult:
    """Résultat complet d'une validation."""
    symbol: str
    timeframe: str
    total_candles: int
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def is_valid(self) -> bool:
        """Valide = pas d'erreurs (les warnings sont acceptables)."""
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def summary(self) -> str:
        lines = [
            f"Validation: {self.symbol} {self.timeframe} ({self.total_candles} candles)",
            f"  Errors:   {len(self.errors)}",
            f"  Warnings: {len(self.warnings)}",
        ]
        for issue in self.issues[:20]:  # Limiter l'affichage
            prefix = "  [ERR]  " if issue.level == "error" else "  [WARN] "
            ts = f" @ {issue.timestamp.isoformat()}" if issue.timestamp else ""
            lines.append(f"{prefix}{issue.rule}: {issue.message}{ts}")
        if len(self.issues) > 20:
            lines.append(f"  ... and {len(self.issues) - 20} more issues")
        return "\n".join(lines)


class DataValidator:
    """
    Valide une liste de Candle selon plusieurs règles d'intégrité.

    Règles appliquées :
    1. not_empty       — au moins une candle
    2. sorted          — timestamps croissants
    3. no_duplicates   — pas de timestamp dupliqué
    4. ohlc_coherent   — low <= open/close <= high, low <= high
    5. prices_positive — open, high, low, close > 0
    6. volume_positive — volume >= 0
    7. no_gaps         — pas de trous dans la série (warning, pas error)
    """

    def validate(
        self,
        candles: list[Candle],
        symbol: str,
        timeframe: str,
        max_gap_ratio: float = 0.01,  # Tolérance : trous < 1% de la série = warning seulement
    ) -> ValidationResult:
        """
        Valide une liste de candles.

        Args:
            candles: Liste à valider
            symbol: Symbole (pour le rapport)
            timeframe: Timeframe (pour la détection de trous)
            max_gap_ratio: % de trous acceptable sans erreur (défaut 1%)

        Returns:
            ValidationResult avec tous les problèmes détectés.
        """
        result = ValidationResult(
            symbol=symbol,
            timeframe=timeframe,
            total_candles=len(candles),
        )

        self._check_not_empty(candles, result)
        if not result.is_valid:
            return result  # Pas de sens de continuer si vide

        self._check_sorted(candles, result)
        self._check_no_duplicates(candles, result)
        self._check_ohlc_coherence(candles, result)
        self._check_prices_positive(candles, result)
        self._check_volume_positive(candles, result)
        self._check_gaps(candles, timeframe, max_gap_ratio, result)

        log_fn = log.warning if not result.is_valid else (
            log.warning if result.has_warnings else log.info
        )
        log_fn(
            "Validation complete",
            symbol=symbol,
            tf=timeframe,
            candles=len(candles),
            errors=len(result.errors),
            warnings=len(result.warnings),
        )

        return result

    # -----------------------------------------------------------------------
    # Règles individuelles
    # -----------------------------------------------------------------------

    def _check_not_empty(self, candles: list[Candle], result: ValidationResult) -> None:
        if not candles:
            result.issues.append(ValidationIssue(
                level="error",
                rule="not_empty",
                message="No candles to validate",
            ))

    def _check_sorted(self, candles: list[Candle], result: ValidationResult) -> None:
        for i in range(1, len(candles)):
            if candles[i].timestamp <= candles[i - 1].timestamp:
                result.issues.append(ValidationIssue(
                    level="error",
                    rule="sorted",
                    message=(
                        f"Candle at index {i} ({candles[i].timestamp}) "
                        f"is not after previous ({candles[i-1].timestamp})"
                    ),
                    candle_index=i,
                    timestamp=candles[i].timestamp,
                ))
                # Un seul signalement suffit — si pas trié le reste n'a pas de sens
                break

    def _check_no_duplicates(self, candles: list[Candle], result: ValidationResult) -> None:
        seen: dict[datetime, int] = {}
        for i, c in enumerate(candles):
            if c.timestamp in seen:
                result.issues.append(ValidationIssue(
                    level="error",
                    rule="no_duplicates",
                    message=(
                        f"Duplicate timestamp {c.timestamp} "
                        f"at indices {seen[c.timestamp]} and {i}"
                    ),
                    candle_index=i,
                    timestamp=c.timestamp,
                ))
            else:
                seen[c.timestamp] = i

    def _check_ohlc_coherence(self, candles: list[Candle], result: ValidationResult) -> None:
        for i, c in enumerate(candles):
            problems = []
            if c.low > c.high:
                problems.append(f"low ({c.low}) > high ({c.high})")
            if c.open < c.low or c.open > c.high:
                problems.append(f"open ({c.open}) outside [low={c.low}, high={c.high}]")
            if c.close < c.low or c.close > c.high:
                problems.append(f"close ({c.close}) outside [low={c.low}, high={c.high}]")

            if problems:
                result.issues.append(ValidationIssue(
                    level="error",
                    rule="ohlc_coherent",
                    message=" | ".join(problems),
                    candle_index=i,
                    timestamp=c.timestamp,
                ))

    def _check_prices_positive(self, candles: list[Candle], result: ValidationResult) -> None:
        for i, c in enumerate(candles):
            if any(p <= 0 for p in (c.open, c.high, c.low, c.close)):
                result.issues.append(ValidationIssue(
                    level="error",
                    rule="prices_positive",
                    message=f"Non-positive price: O={c.open} H={c.high} L={c.low} C={c.close}",
                    candle_index=i,
                    timestamp=c.timestamp,
                ))

    def _check_volume_positive(self, candles: list[Candle], result: ValidationResult) -> None:
        for i, c in enumerate(candles):
            if c.volume < 0:
                result.issues.append(ValidationIssue(
                    level="error",
                    rule="volume_positive",
                    message=f"Negative volume: {c.volume}",
                    candle_index=i,
                    timestamp=c.timestamp,
                ))

    def _check_gaps(
        self,
        candles: list[Candle],
        timeframe: str,
        max_gap_ratio: float,
        result: ValidationResult,
    ) -> None:
        """
        Détecte les trous dans la série temporelle.

        Un trou = intervalle entre deux candles consécutives > expected_interval.
        Les trous sont reportés en warning (pas en error) car ils peuvent
        être légitimes (maintenance Binance, week-end sur certains marchés...).
        """
        try:
            expected_seconds = timeframe_to_seconds(timeframe)
        except ValueError:
            # Timeframe inconnu — on ne peut pas vérifier les trous
            return

        expected_delta = timedelta(seconds=expected_seconds)
        # Tolérance de 10% pour les légères variations de timing
        tolerance = timedelta(seconds=expected_seconds * 0.1)

        gaps: list[tuple[datetime, datetime, int]] = []  # (start, end, missing_count)

        for i in range(1, len(candles)):
            actual_delta = candles[i].timestamp - candles[i - 1].timestamp
            if actual_delta > expected_delta + tolerance:
                missing = int(actual_delta / expected_delta) - 1
                gaps.append((candles[i - 1].timestamp, candles[i].timestamp, missing))

        if not gaps:
            return

        total_missing = sum(g[2] for g in gaps)
        gap_ratio = total_missing / max(len(candles), 1)
        level = "error" if gap_ratio > max_gap_ratio else "warning"

        # Signaler les 5 premiers trous en détail
        for gap_start, gap_end, missing_count in gaps[:5]:
            result.issues.append(ValidationIssue(
                level=level,
                rule="no_gaps",
                message=(
                    f"Gap of {missing_count} missing candle(s) "
                    f"between {gap_start.isoformat()} and {gap_end.isoformat()}"
                ),
                timestamp=gap_start,
            ))

        if len(gaps) > 5:
            result.issues.append(ValidationIssue(
                level=level,
                rule="no_gaps",
                message=f"... and {len(gaps) - 5} more gaps ({total_missing} total missing candles)",
            ))

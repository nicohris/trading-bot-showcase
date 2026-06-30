"""
Interface CLI du bot de trading.

Commandes disponibles :
  python main.py check
  python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01
  python main.py data validate --symbol BTCUSDT --timeframe 1h
  python main.py data info
  python main.py backtest --symbols BTCUSDT,ETHUSDT --start 2024-01-01 [--end 2024-12-31]
  python main.py backtest --symbols BTCUSDT --start 2024-01-01 --verbose
  python main.py paper [--symbols BTCUSDT,ETHUSDT]
  python main.py live  [--symbols BTCUSDT,ETHUSDT]
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_CACHE_DIR = "data/cache"
_DEFAULT_OUTPUT_DIR = "outputs"


@click.group()
@click.option(
    "--config",
    default="config/trading_config.yaml",
    help="Chemin vers trading_config.yaml",
)
@click.option(
    "--log-level",
    default=None,
    help="Override log level (DEBUG, INFO, WARNING)",
)
@click.pass_context
def cli(ctx: click.Context, config: str, log_level: str | None) -> None:
    """Bot de trading crypto — Binance Spot."""
    ctx.ensure_object(dict)

    from config.settings import load_settings
    from core.logger import setup_logging

    settings = load_settings()
    effective_log_level = log_level or settings.log_level
    setup_logging(log_level=effective_log_level, log_format=settings.log_format)

    ctx.obj["settings"] = settings
    ctx.obj["config_path"] = config


# ---------------------------------------------------------------------------
# Commande : check
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def check(ctx: click.Context) -> None:
    """Vérifie la configuration et la connexion à Binance."""
    settings = ctx.obj["settings"]
    click.echo("\n=== Configuration Check ===")

    try:
        from config.settings import load_trading_config
        cfg = load_trading_config()
        click.echo(f"✓ Trading config: {len(cfg.markets)} markets, strategy={cfg.strategy.name}")
        click.echo(f"  Symbols: {', '.join(cfg.symbols)}")
        click.echo(f"  Timeframes: {cfg.execution_timeframe} (exec) / {cfg.trend_timeframe} (trend)")
    except Exception as e:
        click.echo(f"✗ Trading config error: {e}")
        sys.exit(1)

    if settings.trading_mode == "live":
        if not settings.binance_api_key:
            click.echo("✗ BINANCE_API_KEY is not set")
            sys.exit(1)
        click.echo("✓ Binance credentials present")

    if settings.binance_api_key:
        try:
            from binance.client import Client
            client = Client(
                settings.binance_api_key,
                settings.binance_api_secret,
                testnet=settings.binance_testnet,
            )
            info = client.get_exchange_info()
            click.echo(f"✓ Binance connected (timezone={info.get('timezone', 'N/A')})")
        except Exception as e:
            click.echo(f"✗ Binance connection failed: {e}")
            sys.exit(1)
    else:
        click.echo("⚠ Binance credentials not set (OK for backtest, needed for paper/live)")

    if settings.telegram_bot_token:
        click.echo("✓ Telegram token configured")
    else:
        click.echo("⚠ Telegram not configured (notifications disabled)")

    # Vérifier le cache de données
    from data.cache import DataCache
    cache = DataCache(_DEFAULT_CACHE_DIR)
    cached = cache.list_cached()
    if cached:
        click.echo(f"✓ Data cache: {len(cached)} dataset(s) found")
        for sym, tf in cached:
            cov = cache.get_coverage(sym, tf)
            if cov:
                click.echo(f"  {sym} {tf}: {cov[0].date()} → {cov[1].date()}")
    else:
        click.echo("⚠ No data in cache (run: python main.py data download ...)")

    click.echo(f"\nMode: {settings.trading_mode.upper()}")
    click.echo("=== Check passed ===\n")


# ---------------------------------------------------------------------------
# Groupe de commandes : data
# ---------------------------------------------------------------------------


@cli.group()
def data() -> None:
    """Gestion des données historiques (download, validate, info)."""
    pass


@data.command("download")
@click.option("--symbol", required=True, help="Symbole Binance Spot (ex: BTCUSDT)")
@click.option(
    "--timeframe",
    required=True,
    type=click.Choice(["1m", "5m", "15m", "30m", "1h", "4h", "1d"], case_sensitive=False),
    help="Timeframe Binance",
)
@click.option("--start", required=True, help="Date de début (YYYY-MM-DD)")
@click.option("--end", default=None, help="Date de fin (YYYY-MM-DD), défaut: aujourd'hui")
@click.option(
    "--cache-dir",
    default=_DEFAULT_CACHE_DIR,
    show_default=True,
    help="Répertoire du cache local",
)
@click.option(
    "--no-validate",
    is_flag=True,
    default=False,
    help="Désactiver la validation après téléchargement",
)
@click.pass_context
def data_download(
    ctx: click.Context,
    symbol: str,
    timeframe: str,
    start: str,
    end: str | None,
    cache_dir: str,
    no_validate: bool,
) -> None:
    """
    Télécharge les données historiques Binance Spot et les met en cache local.

    Les appels successifs téléchargent uniquement les périodes manquantes (cache-first).

    Exemples :
        python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01
        python main.py data download --symbol ETHUSDT --timeframe 4h --start 2023-01-01 --end 2024-12-31
    """
    from data.downloader import BinanceDownloader
    from data.historical import HistoricalDataLoader

    start_dt = _parse_date(start)
    end_dt = _parse_date(end) if end else datetime.now(timezone.utc)

    click.echo(f"\nDownloading {symbol} {timeframe}")
    click.echo(f"  Period   : {start_dt.date()} → {end_dt.date()}")
    click.echo(f"  Cache dir: {Path(cache_dir).resolve()}")

    client = BinanceDownloader.make_public_client()
    downloader = BinanceDownloader(client)
    loader = HistoricalDataLoader(
        downloader=downloader,
        cache_dir=cache_dir,
        validate=not no_validate,
        stop_on_error=False,
    )

    try:
        candles = loader.load(symbol, timeframe, start_dt, end_dt)
        click.echo(f"\n✓ {len(candles):,} candles loaded")
        click.echo(f"  First : {candles[0].timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
        click.echo(f"  Last  : {candles[-1].timestamp.strftime('%Y-%m-%d %H:%M UTC')}")
        click.echo(f"  File  : {Path(cache_dir) / f'{symbol}_{timeframe}.csv'}")
    except Exception as e:
        click.echo(f"\n✗ Download failed: {e}", err=True)
        sys.exit(1)


@data.command("validate")
@click.option("--symbol", required=True, help="Symbole (ex: BTCUSDT)")
@click.option(
    "--timeframe",
    required=True,
    type=click.Choice(["1m", "5m", "15m", "30m", "1h", "4h", "1d"], case_sensitive=False),
)
@click.option("--start", default=None, help="Filtrer depuis cette date (YYYY-MM-DD)")
@click.option("--end", default=None, help="Filtrer jusqu'à cette date (YYYY-MM-DD)")
@click.option("--cache-dir", default=_DEFAULT_CACHE_DIR, show_default=True)
@click.pass_context
def data_validate(
    ctx: click.Context,
    symbol: str,
    timeframe: str,
    start: str | None,
    end: str | None,
    cache_dir: str,
) -> None:
    """
    Valide l'intégrité des données en cache.

    Vérifie : ordre chronologique, doublons, cohérence OHLCV, trous de données.
    """
    from data.historical import HistoricalDataLoader
    from data.downloader import BinanceDownloader

    start_dt = _parse_date(start) if start else None
    end_dt = _parse_date(end) if end else None

    client = BinanceDownloader.make_public_client()
    downloader = BinanceDownloader(client)
    loader = HistoricalDataLoader(downloader=downloader, cache_dir=cache_dir, validate=False)

    try:
        result = loader.validate_only(symbol, timeframe, start=start_dt, end=end_dt)
    except Exception as e:
        click.echo(f"✗ Validation failed: {e}", err=True)
        sys.exit(1)

    click.echo("\n" + result.summary())

    if result.is_valid and not result.has_warnings:
        click.echo("\n✓ Data is clean")
    elif result.is_valid:
        click.echo(f"\n⚠ Data is valid but has {len(result.warnings)} warning(s)")
    else:
        click.echo(f"\n✗ Data has {len(result.errors)} error(s)")
        sys.exit(1)


@data.command("info")
@click.option("--cache-dir", default=_DEFAULT_CACHE_DIR, show_default=True)
@click.pass_context
def data_info(ctx: click.Context, cache_dir: str) -> None:
    """Affiche le contenu du cache local (symboles, plages de dates, tailles)."""
    from data.cache import DataCache

    cache = DataCache(cache_dir)
    cached = cache.list_cached()

    if not cached:
        click.echo(f"\nNo data in cache ({Path(cache_dir).resolve()})")
        click.echo("\nTo download data:")
        click.echo("  python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01")
        click.echo("  python main.py data download --symbol BTCUSDT --timeframe 4h --start 2023-01-01")
        return

    click.echo(f"\nCache: {Path(cache_dir).resolve()}")
    click.echo(f"{'Symbol':<12} {'TF':<6} {'From':<22} {'To':<22} {'Size':>8}")
    click.echo("-" * 76)

    for symbol, timeframe in cached:
        coverage = cache.get_coverage(symbol, timeframe)
        size_mb = cache.get_cache_size_mb(symbol, timeframe)
        if coverage:
            start_str = coverage[0].strftime("%Y-%m-%d %H:%M")
            end_str = coverage[1].strftime("%Y-%m-%d %H:%M")
        else:
            start_str = end_str = "N/A"
        click.echo(f"{symbol:<12} {timeframe:<6} {start_str:<22} {end_str:<22} {size_mb:>7.2f}M")


# ---------------------------------------------------------------------------
# Commande : backtest
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--symbols",
    default=None,
    help="Symboles à backtester, séparés par virgule (ex: BTCUSDT,ETHUSDT). "
         "Par défaut : tous les symboles activés dans trading_config.yaml",
)
@click.option(
    "--symbol",
    default=None,
    help="Raccourci pour un seul symbole (ex: --symbol BTCUSDT)",
)
@click.option("--start", required=True, help="Date de début (YYYY-MM-DD)")
@click.option(
    "--end",
    default=None,
    help="Date de fin (YYYY-MM-DD). Défaut : aujourd'hui",
)
@click.option(
    "--capital",
    default=None,
    type=float,
    help="Capital initial en USDT (override trading_config.yaml)",
)
@click.option("--cache-dir", default=_DEFAULT_CACHE_DIR, show_default=True)
@click.option(
    "--output-dir",
    default=_DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Répertoire de sortie pour les exports CSV/JSON",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Mode verbeux : affiche chaque événement du backtest (format console lisible)",
)
@click.option(
    "--no-export",
    is_flag=True,
    default=False,
    help="Ne pas exporter les résultats dans outputs/",
)
@click.option(
    "--no-download",
    is_flag=True,
    default=False,
    help="Ne pas télécharger de données si elles sont absentes (échoue si manquantes)",
)
@click.pass_context
def backtest(
    ctx: click.Context,
    symbols: str | None,
    symbol: str | None,
    start: str,
    end: str | None,
    capital: float | None,
    cache_dir: str,
    output_dir: str,
    verbose: bool,
    no_export: bool,
    no_download: bool,
) -> None:
    """
    Lance un backtest sur des données historiques.

    Les données sont chargées depuis le cache local (téléchargement automatique
    si nécessaire et si --no-download n'est pas spécifié).

    Exemples :
        python main.py backtest --symbols BTCUSDT --start 2024-01-01
        python main.py backtest --symbols BTCUSDT,ETHUSDT --start 2023-01-01 --end 2024-12-31
        python main.py backtest --symbols BTCUSDT --start 2024-01-01 --verbose
    """
    from config.settings import load_trading_config
    from core.logger import get_log_file_path, setup_logging

    # --- Mode verbose : reconfigurer le logging en format console ---
    log_file = None
    if verbose:
        setup_logging(log_level="INFO", log_format="console")
        click.echo("\n[VERBOSE MODE] Logging en format console, niveau INFO\n")
    else:
        log_file = get_log_file_path("backtest")
        setup_logging(log_level="INFO", log_format="json", log_file=str(log_file))

    cfg = load_trading_config()

    # --- Résolution des symboles ---
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    elif symbol:
        symbol_list = [symbol.strip().upper()]
    else:
        symbol_list = cfg.symbols

    if not symbol_list:
        click.echo("✗ Aucun symbole spécifié. Utilisez --symbols BTCUSDT ou configurez markets.")
        sys.exit(1)

    start_dt = _parse_date(start)
    end_dt = _parse_date(end) if end else datetime.now(timezone.utc)

    if capital is not None:
        cfg.backtest.initial_capital = capital

    click.echo(f"\n{'='*56}")
    click.echo("  BACKTEST")
    click.echo(f"  Symboles  : {', '.join(symbol_list)}")
    click.echo(f"  Période   : {start_dt.date()} → {end_dt.date()}")
    click.echo(f"  Capital   : {cfg.backtest.initial_capital:,.2f} USDT")
    click.echo(f"  Stratégie : {cfg.strategy.name}")
    if not no_export:
        click.echo(f"  Outputs   : {Path(output_dir).resolve()}")
    if not verbose and log_file:
        click.echo(f"  Log file  : {log_file}")
    click.echo(f"{'='*56}")

    # --- Composants partagés ---
    from backtest.engine import BacktestEngine
    from backtest.reporter import BacktestReporter
    from risk.manager import RiskManager
    from strategy.v1_trend_following import TrendFollowingV1

    reporter = BacktestReporter(output_dir=output_dir)
    all_results = []

    # --- Boucle par symbole ---
    for sym in symbol_list:
        click.echo(f"\n[{sym}] Chargement des données...")

        exec_candles, trend_candles = _load_data(
            symbol=sym,
            start_dt=start_dt,
            end_dt=end_dt,
            cfg=cfg,
            cache_dir=cache_dir,
            no_download=no_download,
        )
        if exec_candles is None:
            continue

        click.echo(
            f"[{sym}] {cfg.execution_timeframe}: {len(exec_candles):,} candles | "
            f"{cfg.trend_timeframe}: {len(trend_candles):,} candles"
        )

        click.echo(f"[{sym}] Exécution du backtest...")
        strategy = TrendFollowingV1()
        risk_manager = RiskManager()
        engine = BacktestEngine(strategy=strategy, risk_manager=risk_manager)

        try:
            result = engine.run(sym, trend_candles, exec_candles)
        except Exception as e:
            click.echo(f"✗ [{sym}] Backtest failed: {e}", err=True)
            if verbose:
                import traceback
                traceback.print_exc()
            continue

        all_results.append(result)
        reporter.print_summary(result)

        if not result.trades:
            click.echo(
                f"  ⚠ Aucun trade généré pour {sym}.\n"
                f"     Vérifiez la période, les paramètres de la stratégie,\n"
                f"     et la quantité de données disponibles (EMA200 nécessite ≥200 bougies)."
            )

        if not no_export and result.trades:
            try:
                trades_path, summary_path = reporter.export(result)
                click.echo(f"  ✓ Trades  : {trades_path}")
                click.echo(f"  ✓ Résumé  : {summary_path}")
            except Exception as e:
                click.echo(f"  ⚠ Export failed: {e}", err=True)

    if len(all_results) > 1:
        reporter.print_combined_summary(all_results)

    if not all_results:
        click.echo("\n✗ Aucun backtest n'a pu être complété.", err=True)
        sys.exit(1)

    if not verbose and log_file:
        click.echo(f"\nLog complet : {log_file}")


# ---------------------------------------------------------------------------
# Commande : paper
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--symbols", default=None, help="Symboles séparés par virgule (override config)")
@click.pass_context
def paper(ctx: click.Context, symbols: str | None) -> None:
    """Lance le bot en mode paper trading (données live, ordres simulés)."""
    settings = ctx.obj["settings"]
    from config.settings import load_trading_config
    from core.logger import setup_logging

    log_file = Path("logs") / "paper.log"
    log_file.parent.mkdir(exist_ok=True)
    setup_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
        log_file=str(log_file),
    )

    cfg = load_trading_config()
    active_symbols = [s.strip().upper() for s in symbols.split(",")] if symbols else cfg.symbols

    click.echo(f"\nStarting paper trading on: {', '.join(active_symbols)}")
    click.echo(f"Log file: {log_file.resolve()}")

    try:
        _run_bot(settings, cfg, active_symbols, mode="paper")
    except KeyboardInterrupt:
        click.echo("\nBot stopped by user")


# ---------------------------------------------------------------------------
# Commande : live
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--symbols", default=None, help="Symboles séparés par virgule (override config)")
@click.confirmation_option(
    prompt="⚠  You are about to start LIVE trading. Real funds at risk. Continue?"
)
@click.pass_context
def live(ctx: click.Context, symbols: str | None) -> None:
    """Lance le bot en mode live trading (ATTENTION : fonds réels)."""
    settings = ctx.obj["settings"]
    from config.settings import load_trading_config

    settings.require_binance_credentials()
    cfg = load_trading_config()
    active_symbols = [s.strip().upper() for s in symbols.split(",")] if symbols else cfg.symbols

    click.echo(f"\nStarting LIVE trading on: {', '.join(active_symbols)}")

    try:
        _run_bot(settings, cfg, active_symbols, mode="live")
    except KeyboardInterrupt:
        click.echo("\nBot stopped by user")


# ---------------------------------------------------------------------------
# Runtime interne
# ---------------------------------------------------------------------------


def _run_bot(settings, cfg, symbols: list[str], mode: str) -> None:
    """Lance la boucle principale du bot (paper ou live)."""
    from runtime.paper import PaperRuntime

    log.info("Bot runtime starting", mode=mode, symbols=symbols)

    initial_equity = cfg.backtest.initial_capital
    runtime = PaperRuntime(symbols=symbols, initial_equity=initial_equity)
    runtime.run()


# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------


def _parse_date(date_str: str) -> datetime:
    """Parse une date YYYY-MM-DD en datetime UTC."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise click.BadParameter(
            f"Format invalide : '{date_str}'. Attendu : YYYY-MM-DD",
            param_hint="date",
        )


def _load_data(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    cfg,
    cache_dir: str,
    no_download: bool,
) -> tuple[list | None, list | None]:
    """
    Charge les données historiques pour un symbole.

    Retourne (exec_candles, trend_candles) ou (None, None) en cas d'échec.
    """
    from data.cache import DataCache
    from data.downloader import BinanceDownloader
    from data.historical import HistoricalDataLoader

    exec_tf = cfg.execution_timeframe
    trend_tf = cfg.trend_timeframe

    cache = DataCache(cache_dir)
    missing_tfs = [tf for tf in (exec_tf, trend_tf) if not cache.exists(symbol, tf)]

    if missing_tfs and no_download:
        click.echo(
            f"\n✗ [{symbol}] Données manquantes pour {', '.join(missing_tfs)}.\n"
            f"   --no-download est actif. Téléchargez d'abord avec :\n"
            f"   python main.py data download --symbol {symbol} --timeframe {missing_tfs[0]} --start {start_dt.date()}"
        )
        return None, None

    if missing_tfs:
        click.echo(f"[{symbol}] Données manquantes pour {', '.join(missing_tfs)} — téléchargement...")

    if missing_tfs:
        client = BinanceDownloader.make_public_client()
        downloader = BinanceDownloader(client)
    else:
        downloader = None
    loader = HistoricalDataLoader(
        downloader=downloader,
        cache_dir=cache_dir,
        validate=True,
        stop_on_error=False,
    )

    try:
        data_by_tf = loader.load_multi_timeframe(
            symbol=symbol,
            start=start_dt,
            end=end_dt,
            timeframes=[exec_tf, trend_tf],
        )
    except Exception as e:
        click.echo(f"\n✗ [{symbol}] Erreur de chargement des données : {e}", err=True)
        return None, None

    exec_candles = data_by_tf.get(exec_tf, [])
    trend_candles = data_by_tf.get(trend_tf, [])

    if not exec_candles:
        click.echo(f"\n✗ [{symbol}] Aucune donnée {exec_tf} pour {start_dt.date()} → {end_dt.date()}.")
        return None, None

    if not trend_candles:
        click.echo(f"\n✗ [{symbol}] Aucune donnée {trend_tf} pour {start_dt.date()} → {end_dt.date()}.")
        return None, None

    return exec_candles, trend_candles

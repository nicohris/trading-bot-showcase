# 📈 Crypto Trading Bot — Modular Framework (Binance Spot)

> A complete algorithmic trading engine in Python: **backtest → paper trading → live**,
> with the *same* strategy and risk manager shared across all three modes, and a
> *walk-forward* backtester that is guaranteed free of lookahead bias by construction.
> Deployed 24/7 in paper trading on a cloud VM.

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-pytest-brightgreen)
![Status](https://img.shields.io/badge/status-running%20live%20(paper)-success)

---

## In one sentence

A crypto **trend-following** bot for Binance Spot, designed as a *system* that
produces a concrete output (signals → orders → measurable PnL), with a strict
layered architecture so that what you backtest is exactly what runs in production.

> ℹ️ **About this repository (showcase).** The code published here includes the
> full framework and a documented **reference strategy** (`TrendFollowingV1`) used
> to demonstrate the end-to-end architecture. The strategy variant actually
> deployed live (calibrated parameters, additional regime filters) is kept
> private — only its **broad outline** is described below.

---

## 🎯 What this project demonstrates

- **Clean software architecture** — decoupled layers (`core`, `data`, `strategy`,
  `risk`, `execution`, `portfolio`, `backtest`) with a strict *dependency rule*:
  the strategy knows nothing about the exchange, the risk layer, or the portfolio.
  Everything flows through shared `core` objects.
- **Real-time API integration** — Binance market-data fetching (REST), kline
  handling, multi-timeframe synchronization.
- **Lookahead-free walk-forward backtester** — the signal is generated at the
  close of candle *N*, the entry happens at the open of *N+1*, and the
  multi-timeframe data is filtered by timestamp: **zero future-information leakage
  by construction**.
- **Production-grade risk management** — fixed-fractional position sizing,
  ATR-based stop-loss, partial take-profit, break-even, trailing stop, plus
  portfolio guardrails (max exposure, consecutive losses, daily loss limit).
- **Quality & reliability** — `pytest` test suite, structured logging
  (`structlog`), typed configuration (`pydantic`), ergonomic CLI (`click`).
- **Actually deployed** — runs **24/7** in paper trading on a Linux cloud VM via a
  `systemd` service (see [DEPLOYMENT.md](DEPLOYMENT.md)).

---

## 🏗️ Architecture

```
        DataProvider ──► StrategyContext
                              │
                              ▼
                   StrategyBase.generate_signal()
                              │
                              ▼
                           Signal
                              │
                              ▼
                  RiskManager.validate_signal()   ◄── guardrails + sizing
                              │
                              ▼
                        OrderRequest
                              │
                              ▼
                   ExecutorBase.execute()          ◄── paper | live
                              │
                              ▼
                            Order
                              │
                              ▼
              PortfolioManager.open_position()      ◄── positions, PnL, history
```

| Mode      | DataProvider          | Executor       |
|-----------|-----------------------|----------------|
| backtest  | `BacktestDataProvider`| `PaperExecutor`|
| paper     | `BinanceFetcher`      | `PaperExecutor`|
| live      | `BinanceFetcher`      | `LiveExecutor` |

**The strategy and the RiskManager are identical across all three modes.** This is
the central guarantee of the project: *what you backtest is what runs live*.

### Dependency rule

- `strategy` depends on neither `exchange`, `risk`, nor `portfolio`.
- `risk` depends on neither `exchange` nor `strategy`.
- Everything flows through the `core` models (`Candle`, `Signal`, `OrderRequest`,
  `Order`, `Position`, `Trade`…).

---

## 🧠 The strategy (broad outline)

The reference strategy is a **multi-timeframe, long-only trend-following** system:

1. **Trend filter** on the slow timeframe (4h) — moving-average structure + slope,
   to trade only in the direction of the underlying trend.
2. **Confirmation** on the fast timeframe (1h) — price / moving-average alignment.
3. **Market-quality filters** — sufficient volatility and participation (volume) to
   avoid false signals in flat markets.
4. **Entry setups** — recent-high *breakout* **or** continuation *pullback* onto a
   moving average.

**Position management**: ATR-based stop-loss, partial take-profit at a risk
multiple (1R), move to break-even, then *trailing stop* on the remainder. Risk
capped at a small percentage of capital per trade, with portfolio-level ceilings.

> The production variant adds parameter calibration and additional market-regime
> filters — not included in this showcase repository.

---

## 📊 Results (cross-asset validation, aggregated)

Methodology: *walk-forward* backtest over **3 years of data (2022–2024)**, applied
to **6 major assets** (BTC, ETH, BNB, SOL, XRP, ADA) across multiple sub-periods,
with simulated fees and slippage.

| Metric                              | Value (aggregated)      |
|-------------------------------------|-------------------------|
| Tested period                       | 2022 → 2024 (3 years)   |
| Positive asset × period combinations| ~72 %                   |
| Maximum observed drawdown           | < 3.5 %                 |
| Risk per trade                      | 1 % of capital          |

> Aggregated figures shown to illustrate the **approach** (cross-asset robustness,
> drawdown control). Past backtested performance does not guarantee future results.
> **This is not financial advice.**

---

## 🚀 Quick start

### Requirements
- Python 3.11+

### Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # edit if needed (keys optional for paper mode)
```

### Download data (public API, no key required)

```bash
python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01
python main.py data download --symbol BTCUSDT --timeframe 4h --start 2023-01-01
python main.py data info
```

### Run a backtest

```bash
python main.py backtest --symbols BTCUSDT,ETHUSDT --start 2024-01-01 --end 2024-12-31
python main.py backtest --symbol BTCUSDT --start 2024-01-01 --verbose   # step-by-step demo mode
```

### Paper trading (live data, simulated orders)

```bash
python main.py paper
python main.py paper --symbols BTCUSDT,ETHUSDT
```

### Check configuration / run tests

```bash
python main.py check
pytest
```

Windows helper scripts (`run_backtest.bat`, `run_demo.bat`, `run_tests.bat`,
`run_data_download.bat`) are provided for convenience.

---

## 📁 Project structure

```
.
├── main.py                  # CLI entry point
├── config/                  # Settings (.env) + trading_config.yaml (pydantic-typed)
├── core/                    # Models, enums, exceptions, logger — no external deps
├── data/                    # DataProvider, Binance fetcher, cache, loader, validator
├── exchange/                # Exchange interface + Binance Spot implementation
├── strategy/                # StrategyBase, indicators, reference strategy V1
├── risk/                    # RiskManager + position sizing
├── execution/               # Paper & live executors
├── portfolio/               # Position tracking, PnL, history
├── backtest/                # Walk-forward engine + metrics + reporting
├── notifications/           # Telegram notifier (optional)
├── storage/                 # SQLite persistence (SQLAlchemy)
├── runtime/                 # Real-time paper trading loop
├── cli/                     # Click commands (check, data, backtest, paper, live)
├── tests/                   # pytest suite
└── deploy/                  # systemd service example
```

---

## 🛠️ Tech stack

`Python 3.11` · `pandas` / `numpy` · `pydantic` · `click` · `structlog` ·
`python-binance` · `SQLAlchemy` · `pytest`

---

## ⚠️ Disclaimer

Educational and technical-demonstration project. Trading crypto assets carries a
risk of capital loss. **This repository is not investment advice.** Use `live` mode
at your own risk, with restricted API keys (spot only, no withdrawal).

## 📄 License

[MIT](LICENSE)

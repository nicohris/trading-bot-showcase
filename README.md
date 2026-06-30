# 📈 Crypto Trading Bot — framework modulaire (Binance Spot)

> Un moteur de trading algorithmique complet en Python : **backtest → paper trading → live**,
> une même stratégie et un même risk manager partagés entre les trois modes, et un
> backtester *walk-forward* garanti sans biais de lookahead. Déployé en paper trading
> 24/7 sur une VM cloud.

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-pytest-brightgreen)
![Status](https://img.shields.io/badge/status-running%20live%20(paper)-success)

---

## En une phrase

Un bot de trading crypto **trend-following** sur Binance Spot, pensé comme un
*système* qui produit un output concret (signaux → ordres → PnL mesurable), avec
une architecture en couches stricte qui permet de tester en backtest exactement ce
qui tourne en production.

> ℹ️ **À propos de ce dépôt (vitrine).** Le code publié ici inclut le framework
> complet et une **stratégie de référence** documentée (`TrendFollowingV1`) qui
> sert à démontrer l'architecture de bout en bout. La variante de stratégie
> réellement déployée en live (paramètres calibrés, filtres de régime
> additionnels) est gardée privée — seules ses **grandes lignes** sont décrites
> ci-dessous.

---

## 🎯 Ce que ce projet démontre

- **Architecture logicielle propre** — couches découplées (`core`, `data`,
  `strategy`, `risk`, `execution`, `portfolio`, `backtest`) avec une *règle de
  dépendances* stricte : la stratégie ne connaît ni l'exchange, ni le risque, ni
  le portefeuille. Tout transite par des objets `core` partagés.
- **Intégration d'API temps réel** — récupération des données de marché Binance
  (REST), gestion des klines, synchronisation multi-timeframe.
- **Backtester walk-forward sans lookahead** — le signal est généré à la clôture
  de la bougie *N*, l'entrée se fait à l'ouverture de *N+1*, et le multi-timeframe
  est filtré par horodatage : **zéro fuite d'information future par construction**.
- **Gestion du risque de niveau production** — sizing en *fixed-fractional*,
  stop-loss basé sur l'ATR, prise partielle, break-even, trailing stop, et
  garde-fous portefeuille (exposition max, pertes consécutives, perte journalière).
- **Qualité & fiabilité** — suite de tests `pytest`, logs structurés (`structlog`),
  configuration typée (`pydantic`), CLI ergonomique (`click`).
- **Déployé pour de vrai** — tourne en paper trading **24/7** sur une VM cloud
  Linux via un service `systemd` (voir [DEPLOYMENT.md](DEPLOYMENT.md)).

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
                  RiskManager.validate_signal()   ◄── garde-fous + sizing
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
              PortfolioManager.open_position()      ◄── positions, PnL, historique
```

| Mode      | DataProvider          | Executor       |
|-----------|-----------------------|----------------|
| backtest  | `BacktestDataProvider`| `PaperExecutor`|
| paper     | `BinanceFetcher`      | `PaperExecutor`|
| live      | `BinanceFetcher`      | `LiveExecutor` |

**La stratégie et le RiskManager sont identiques dans les trois modes.** C'est la
garantie centrale du projet : *ce que l'on backteste est ce qui tourne en live*.

### Règle de dépendances

- `strategy` ne dépend ni de `exchange`, ni de `risk`, ni de `portfolio`.
- `risk` ne dépend ni de `exchange`, ni de `strategy`.
- Tout passe par les modèles `core` (`Candle`, `Signal`, `OrderRequest`, `Order`,
  `Position`, `Trade`…).

---

## 🧠 La stratégie (grandes lignes)

La stratégie de référence est un **trend-following multi-timeframe long-only** :

1. **Filtre de tendance** sur le timeframe lent (4h) — structure de moyennes
   mobiles + pente, pour ne trader que dans le sens de la tendance de fond.
2. **Confirmation** sur le timeframe rapide (1h) — alignement prix / moyennes.
3. **Filtres de qualité de marché** — volatilité et participation (volume)
   suffisantes pour éviter les faux signaux en marché atone.
4. **Setups d'entrée** — *breakout* de plus-haut récent **ou** *pullback* de
   continuation sur moyenne mobile.

**Gestion de position** : stop-loss basé sur l'ATR, prise partielle à un multiple
de risque (1R), passage à break-even, puis *trailing stop* sur le solde. Risque
limité à un faible pourcentage du capital par trade, avec plafonds au niveau du
portefeuille.

> La variante déployée en production ajoute une calibration des paramètres et des
> filtres de régime de marché supplémentaires — non inclus dans ce dépôt vitrine.

---

## 📊 Résultats (validation cross-actifs, agrégés)

Méthodologie : backtest *walk-forward* sur **3 ans de données (2022–2024)**,
appliqué à **6 actifs majeurs** (BTC, ETH, BNB, SOL, XRP, ADA) sur plusieurs
sous-périodes, frais et slippage simulés.

| Métrique                              | Valeur (agrégée)        |
|---------------------------------------|-------------------------|
| Période testée                        | 2022 → 2024 (3 ans)     |
| Combinaisons actif × période positives| ~72 %                   |
| Drawdown maximum observé              | < 3,5 %                 |
| Risque par trade                      | 1 % du capital          |

> Chiffres agrégés à titre d'illustration de la **démarche** (robustesse
> cross-actifs, contrôle du drawdown). Les performances passées en backtest ne
> préjugent pas des performances futures. **Ceci n'est pas un conseil financier.**

---

## 🚀 Démarrage rapide

### Prérequis
- Python 3.11+

### Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # éditer si besoin (clés optionnelles pour le paper)
```

### Télécharger des données (API publique, aucune clé requise)

```bash
python main.py data download --symbol BTCUSDT --timeframe 1h --start 2023-01-01
python main.py data download --symbol BTCUSDT --timeframe 4h --start 2023-01-01
python main.py data info
```

### Lancer un backtest

```bash
python main.py backtest --symbols BTCUSDT,ETHUSDT --start 2024-01-01 --end 2024-12-31
python main.py backtest --symbol BTCUSDT --start 2024-01-01 --verbose   # mode démo pas-à-pas
```

### Paper trading (données live, ordres simulés)

```bash
python main.py paper
python main.py paper --symbols BTCUSDT,ETHUSDT
```

### Vérifier la configuration / lancer les tests

```bash
python main.py check
pytest
```

Des scripts Windows (`run_backtest.bat`, `run_demo.bat`, `run_tests.bat`,
`run_data_download.bat`) sont fournis pour aller plus vite.

---

## 📁 Structure du projet

```
.
├── main.py                  # Point d'entrée CLI
├── config/                  # Settings (.env) + trading_config.yaml (typé pydantic)
├── core/                    # Modèles, enums, exceptions, logger — aucune dépendance externe
├── data/                    # DataProvider, fetcher Binance, cache, loader, validateur
├── exchange/                # Interface exchange + implémentation Binance Spot
├── strategy/                # StrategyBase, indicateurs, stratégie de référence V1
├── risk/                    # RiskManager + position sizing
├── execution/               # Executors paper & live
├── portfolio/               # Suivi des positions, PnL, historique
├── backtest/                # Moteur walk-forward + métriques + reporting
├── notifications/           # Notifier Telegram (optionnel)
├── storage/                 # Persistance SQLite (SQLAlchemy)
├── runtime/                 # Boucle de paper trading temps réel
├── cli/                     # Commandes Click (check, data, backtest, paper, live)
├── tests/                   # Suite pytest
└── deploy/                  # Exemple de service systemd
```

---

## 🛠️ Stack technique

`Python 3.11` · `pandas` / `numpy` · `pydantic` · `click` · `structlog` ·
`python-binance` · `SQLAlchemy` · `pytest`

---

## ⚠️ Avertissement

Projet à but pédagogique et de démonstration technique. Le trading de
crypto-actifs comporte un risque de perte en capital. **Ce dépôt ne constitue
pas un conseil en investissement.** Utilisez le mode `live` à vos propres risques,
avec des clés API restreintes (spot, sans retrait).

## 📄 Licence

[MIT](LICENSE) — pensez à renseigner votre nom dans le fichier `LICENSE`.

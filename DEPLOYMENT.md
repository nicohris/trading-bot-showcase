# Déploiement — paper trading 24/7

Ce bot tourne en continu sur une VM cloud Linux (testé sur Ubuntu 22.04 LTS,
ARM/x86), en mode **paper trading** : données de marché réelles via l'API
publique Binance, ordres simulés en mémoire. Aucune clé API n'est requise pour
le paper trading.

> Les commandes ci-dessous utilisent des **placeholders** (`<APP_DIR>`,
> `<SERVICE_USER>`, `<VM_IP>`). Remplacez-les par vos propres valeurs.

## 1. Préparer la VM

```bash
ssh <SERVICE_USER>@<VM_IP>

sudo apt update && sudo apt install -y python3.11 python3.11-venv git
git clone <YOUR_REPO_URL> <APP_DIR>
cd <APP_DIR>

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. Configurer l'environnement

```bash
cp .env.example .env
nano .env   # TRADING_MODE=paper ; les clés Binance peuvent rester vides
```

Vérifier la configuration :

```bash
python main.py check
```

## 3. Lancer en service systemd (démarrage auto + restart)

```bash
# Adapter les placeholders dans le fichier d'exemple, puis :
sudo cp deploy/trading-bot.service.example /etc/systemd/system/trading-bot.service
sudo nano /etc/systemd/system/trading-bot.service   # remplacer <...>

sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot
sudo systemctl status trading-bot
```

## 4. Observer

```bash
# Logs applicatifs
tail -f <APP_DIR>/logs/paper.log

# Logs systemd
journalctl -u trading-bot -f

# Trades
grep -E "Position (opened|closed)|Partial take" <APP_DIR>/logs/paper.log | tail -20
```

## 5. Mettre à jour

```bash
sudo systemctl stop trading-bot
cd <APP_DIR> && git pull
source .venv/bin/activate && pip install -e . --quiet
sudo systemctl start trading-bot
```

## Notes de sécurité

- Ne committez jamais `.env` ni de clés API (déjà couvert par `.gitignore`).
- Pour le mode `live`, créez des clés API Binance **restreintes au spot, sans
  retrait**, et stockez-les uniquement dans `.env` sur la VM.
- Restreignez l'accès SSH (clé uniquement, pas de mot de passe) et n'ouvrez que
  le port strictement nécessaire.

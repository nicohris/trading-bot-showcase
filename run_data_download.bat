@echo off
cd /d "%~dp0"

echo.
echo [INFO] Dossier courant : %CD%

python --version > nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python non trouve dans le PATH.
    pause
    exit /b 1
)

if not exist "main.py" (
    echo [ERREUR] main.py introuvable dans %CD%
    echo         Ce script doit etre dans le dossier racine du projet.
    pause
    exit /b 1
)

if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activation du venv ...
    call ".venv\Scripts\activate.bat"
) else (
    echo [WARN] Pas de .venv trouve. Python systeme utilise.
)

echo.
echo [INFO] Python utilise :
where python

set START_DATE=2023-01-01

echo.
echo ============================================================
echo  Telechargement des donnees depuis %START_DATE%
echo ============================================================

echo.
echo [1/2] BTCUSDT 1h ...
python main.py data download --symbol BTCUSDT --timeframe 1h --start %START_DATE%
if errorlevel 1 (
    echo [ERREUR] Echec BTCUSDT 1h
    pause
    exit /b 1
)

echo.
echo [2/2] ETHUSDT 1h ...
python main.py data download --symbol ETHUSDT --timeframe 1h --start %START_DATE%
if errorlevel 1 (
    echo [ERREUR] Echec ETHUSDT 1h
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Telechargement termine. Contenu du cache :
echo ============================================================
python main.py data info

echo.
echo [OK] Donnees pretes. Lancez run_backtest.bat ou run_demo.bat
echo.
pause
exit /b 0

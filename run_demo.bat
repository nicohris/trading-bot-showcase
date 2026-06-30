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

set SYMBOL=BTCUSDT
set START_DATE=2024-01-01
set END_DATE=2024-03-31

echo.
echo ============================================================
echo  DEMO MODE -- %SYMBOL% -- %START_DATE% to %END_DATE%
echo  Chaque evenement du bot sera visible en console
echo ============================================================
echo.

python main.py backtest --symbols %SYMBOL% --start %START_DATE% --end %END_DATE% --verbose --no-export

if errorlevel 1 (
    echo.
    echo [ERREUR] La demo a echoue.
    echo [HINT]   Si les donnees sont absentes : lancez run_data_download.bat
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Demo terminee.
echo.
pause
exit /b 0

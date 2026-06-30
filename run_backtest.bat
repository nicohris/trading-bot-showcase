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

set SYMBOLS=BTCUSDT,ETHUSDT
set START_DATE=2024-01-01
set END_DATE=2024-12-31
REM set CAPITAL=10000

echo.
echo ============================================================
echo  BACKTEST -- %SYMBOLS% -- %START_DATE% to %END_DATE%
echo ============================================================
echo.

set CMD=python main.py backtest --symbols %SYMBOLS% --start %START_DATE% --end %END_DATE%
if defined CAPITAL set CMD=%CMD% --capital %CAPITAL%

%CMD%

if errorlevel 1 (
    echo.
    echo [ERREUR] Le backtest a echoue.
    echo         Consultez les logs dans le dossier logs\
    echo         Si les donnees sont absentes : lancez run_data_download.bat
    pause
    exit /b 1
)

echo.
echo [OK] Backtest termine. Resultats dans : outputs\
echo.
pause
exit /b 0

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

if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Activation du venv ...
    call ".venv\Scripts\activate.bat"
) else (
    echo [WARN] Pas de .venv trouve. Python systeme utilise.
    echo        Pour creer un venv : python -m venv .venv
)

echo.
echo [INFO] Python utilise :
where python

echo.
echo ============================================================
echo  Lancement des tests ...
echo ============================================================
echo.

python -m pytest tests\ -v --tb=short %*

set TEST_EXIT=%ERRORLEVEL%

echo.
echo ============================================================
if %TEST_EXIT% EQU 0 (
    echo  [OK] Tous les tests sont passes.
) else (
    echo  [FAIL] Des tests ont echoue ^(code %TEST_EXIT%^).
)
echo ============================================================
echo.
pause
exit /b %TEST_EXIT%

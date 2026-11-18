@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Anonymiseur de visages : compilation du .exe ===

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

if not exist .venv (
    echo [1/5] Creation de l'environnement Python...
    %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat

echo [2/5] Installation des dependances (versions figees)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :err

echo [3/5] Modele de detection IA YuNet (telechargement + verification SHA-256)...
python tools_model.py
if errorlevel 1 (
    echo.
    echo   ! Modele IA indisponible : le .exe utiliserait la detection BASIQUE, qui rate plus de visages.
    choice /M "  Compiler quand meme"
    if errorlevel 2 goto :err
)

echo [4/5] Tests automatiques...
python -m pytest -q tests || goto :err

echo [5/5] Compilation avec PyInstaller...
pyinstaller --noconfirm --clean AnonymiseurVisages.spec || goto :err

echo Auto-test du .exe...
start /wait "" dist\AnonymiseurVisages.exe --selftest --log "%TEMP%\anonymiseur_selftest.txt"
type "%TEMP%\anonymiseur_selftest.txt"
echo.
echo === Termine : dist\AnonymiseurVisages.exe ===
explorer dist
exit /b 0

:err
echo.
echo *** ERREUR pendant la compilation ***
pause
exit /b 1

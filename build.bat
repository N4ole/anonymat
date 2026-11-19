@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Anonymiseur de visages : compilation du .exe ===

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

rem Python du .venv appele directement : activate.bat garde le chemin de creation du
rem .venv et, si le dossier a ete deplace, le Python GLOBAL serait utilise sans prevenir.
set VPY=.venv\Scripts\python.exe
if exist "%VPY%" (
    "%VPY%" -c "import sys; sys.exit(sys.prefix == sys.base_prefix)" >nul 2>nul || (
        echo   ! .venv inutilisable : il est recree.
        rmdir /s /q .venv
    )
)
if not exist "%VPY%" (
    echo [1/5] Creation de l'environnement Python...
    %PY% -m venv .venv || goto :err
)

echo [2/5] Installation des dependances (versions figees)...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt || goto :err

echo [3/5] Modele de detection IA YuNet (telechargement + verification SHA-256)...
"%VPY%" tools_model.py
if errorlevel 1 (
    echo.
    echo   ! Modele IA indisponible : le .exe utiliserait la detection BASIQUE, qui rate plus de visages.
    choice /M "  Compiler quand meme"
    if errorlevel 2 goto :err
)
if not exist ffmpeg\ffmpeg.exe (
    echo   i ffmpeg absent : le son des videos sera toujours supprime ^(voix deformee indisponible^).
    echo     Voir README, section "Compiler soi-meme".
)

echo [4/5] Tests automatiques...
"%VPY%" -m pytest -q tests || goto :err

echo [5/5] Compilation avec PyInstaller...
rem Dossier de travail hors de OneDrive : la synchronisation verrouille les fichiers temporaires.
set WORK=%LOCALAPPDATA%\AnonymiseurVisages\build
"%VPY%" -m PyInstaller --noconfirm --clean --workpath "%WORK%" AnonymiseurVisages.spec || goto :err

echo Auto-test du .exe...
del "%TEMP%\anonymiseur_selftest.txt" 2>nul
start /wait "" dist\AnonymiseurVisages.exe --selftest --log "%TEMP%\anonymiseur_selftest.txt"
type "%TEMP%\anonymiseur_selftest.txt"
echo.
findstr /C:"RESULTAT=OK" "%TEMP%\anonymiseur_selftest.txt" >nul || (echo *** Auto-test en ECHEC *** & goto :err)
echo === Termine : dist\AnonymiseurVisages.exe ===
explorer dist
exit /b 0

:err
echo.
echo *** ERREUR pendant la compilation ***
pause
exit /b 1

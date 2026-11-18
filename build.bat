@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Anonymiseur de visages : compilation du .exe ===

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

if not exist .venv (
    echo [1/4] Creation de l'environnement Python...
    %PY% -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat

echo [2/4] Installation des dependances...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :err

echo [3/4] Telechargement du modele de detection YuNet (IA, ~230 Ko)...
if not exist models mkdir models
if not exist models\face_detection_yunet_2023mar.onnx (
    curl -L --fail -o models\face_detection_yunet_2023mar.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
    if errorlevel 1 (
        del models\face_detection_yunet_2023mar.onnx 2>nul
        echo   ! Telechargement impossible : le .exe utilisera la detection Haar, moins precise.
    )
)

echo [4/4] Compilation avec PyInstaller...
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name AnonymiseurVisages ^
  --icon icon.ico ^
  --add-data "icon.ico;." ^
  --add-data "haar;haar" ^
  --add-data "models;models" ^
  --hidden-import PIL._tkinter_finder ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module pandas ^
  app.py || goto :err

echo.
echo === Termine : dist\AnonymiseurVisages.exe ===
explorer dist
exit /b 0

:err
echo.
echo *** ERREUR pendant la compilation ***
pause
exit /b 1

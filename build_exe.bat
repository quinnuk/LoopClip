@echo off
REM build_exe.bat - rebuilds LoopClip.exe from source
REM Run this after making any change to the .py files.

cd /d "%~dp0"

echo Installing/updating dependencies...
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

echo.
echo Building LoopClip.exe...
python -m PyInstaller --noconfirm --onefile --windowed --icon icon.ico --name LoopClip main.py

REM PyInstaller --onefile only writes the exe into dist\ - copy it up to
REM the project root so it's where the README/users expect it.
if exist "dist\LoopClip.exe" (
    copy /y "dist\LoopClip.exe" "LoopClip.exe" >nul
)

if exist "LoopClip.exe" (
    echo.
    echo Build succeeded - LoopClip.exe is ready in this folder.
) else (
    echo.
    echo Build did not produce LoopClip.exe - check the output above for errors.
)

pause
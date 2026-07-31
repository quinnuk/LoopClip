@echo off
rem Aquarium Downloader launcher.
rem Uses pythonw (the windowless Python interpreter) and "start" so no
rem console window stays open or flashes on screen.
cd /d "%~dp0"
start "" pythonw main.py

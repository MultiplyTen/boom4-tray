@echo off
chcp 65001 >nul
if exist "%~dp0dist\BOOM4-Tray\BOOM4-Tray.exe" (
    start "" "%~dp0dist\BOOM4-Tray\BOOM4-Tray.exe"
    exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0boom_tray.py"
    exit /b 0
)
echo 未找到运行环境。请使用已打包的 BOOM4-Tray.exe，或按 README 创建 .venv。
pause
exit /b 1


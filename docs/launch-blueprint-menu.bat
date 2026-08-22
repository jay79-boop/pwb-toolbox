@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:menu
cls
echo.
echo ========================================
echo.   BLUEPRINT OPERATIONS FRAMEWORK
echo.
echo   Pick a tool to launch:
echo.
echo   1 = Dashboard (Visual Connections)
echo   2 = Builder (Create Blueprint)
echo   3 = Guide (Documentation)
echo.
echo   Q = Quit
echo.
echo ========================================
echo.

set /p choice="Your choice (1, 2, 3, or Q): "

if /i "%choice%"=="1" (
    start "" "blueprint-dashboard.html"
    exit /b 0
)

if /i "%choice%"=="2" (
    start "" "blueprint-builder.html"
    exit /b 0
)

if /i "%choice%"=="3" (
    start "" "blueprint-index.md"
    exit /b 0
)

if /i "%choice%"=="Q" (
    exit /b 0
)

echo.
echo Invalid choice. Try again.
timeout /t 2 /nobreak
goto menu

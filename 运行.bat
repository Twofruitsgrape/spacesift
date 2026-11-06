@echo off
rem Keep this file ASCII-only: cmd.exe reads .bat in the console code page
rem (GBK on zh-CN), and any non-ASCII byte here breaks the parser.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

set "PYTHONPATH=%CD%\src"

echo ============================================
echo   SpaceSift - dry-run by default
echo ============================================
echo.

%PY% -m spacesift %*

echo.
echo To delete safe items for real:  spacesift clean --yes
if not defined DISKGUARD_NO_PAUSE pause

@echo off
rem Serve the built site locally.
rem   Usage:  tools\serve_local.cmd [port] [target]
rem     port   = default 8899
rem     target = gh (default, static-mirror layout) | cf (deployment layout)
rem
rem Why a .cmd: the project convention is "no PowerShell dependency on target machines".
rem Note for editors: this file MUST stay CRLF -- cmd.exe eats the first character of a
rem `rem` line if it is LF-only (that bit us on 2026-10-02, see .gitattributes).
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=8899
set TARGET=%2
if "%TARGET%"=="" set TARGET=gh
set ROOT=%~dp0..
cd /d "%ROOT%"
if /i "%TARGET%"=="cf" (set DIR=dist) else (set DIR=dist-gh)
if not exist "%DIR%\index.html" (
  echo [serve_local] %DIR%\index.html not found -- build it first:
  echo   node tools\build_dist.mjs --target %TARGET% --out %DIR%
  exit /b 1
)
echo [serve_local] serving %DIR% on http://127.0.0.1:%PORT%/   ^(Ctrl+C to stop^)
python -m http.server %PORT% --bind 127.0.0.1 --directory "%DIR%"
endlocal

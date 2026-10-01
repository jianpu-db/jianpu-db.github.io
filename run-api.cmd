@echo off
rem 简谱旋律查歌 —— 起**写后端**（FastAPI 版）。ASCII only（见 DEPLOY.md：.cmd 里放中文会被 932 代码页吃坏）。
rem
rem 用法:
rem   run-api.cmd            起在 8770（与旧版同端口；隧道/Worker 都指着它）
rem   run-api.cmd 8790       换端口
rem
rem 依赖用 uv 管（pyproject.toml + uv.lock）: 有 uv 就用 uv run；没有就退回系统 Python。
rem 旧的标准库版随时可退回:  py app\server.py 8770
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=8770

if "%JPSUBMIT_TOKEN%"=="" (
  echo [!] JPSUBMIT_TOKEN not set -- write API will accept anyone on this port.
  echo     Set it first:  set JPSUBMIT_TOKEN=your-secret
)

where uv >nul 2>nul
if %ERRORLEVEL%==0 (
  echo [i] using uv ^(pyproject.toml + uv.lock^)
  uv run python app\api.py %PORT%
) else (
  echo [i] uv not found -- using system python
  python app\api.py %PORT%
)
endlocal

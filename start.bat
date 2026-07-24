@echo off
chcp 65001 >nul
title 工程图纸智能审查平台

echo ═══════════════════════════════════════════════════
echo   工程图纸智能审查平台 v1.0
echo   启动中...
echo ═══════════════════════════════════════════════════

REM 检查 Python
python --version 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo [信息] 创建虚拟环境...
    python -m venv .venv
)

REM 安装依赖
echo [信息] 检查依赖...
.venv\Scripts\pip install -r requirements.txt --quiet

REM 设置环境变量（可选）
REM set ODA_PATH=C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe
REM set OPENAI_API_KEY=sk-your-key-here

REM 启动服务
echo.
echo [成功] 服务已启动
echo [信息] 请在浏览器打开: http://localhost:8000
echo [信息] 按 Ctrl+C 停止服务
echo.

.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pause

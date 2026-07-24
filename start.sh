#!/bin/bash
# ═══════════════════════════════════════════════════
#   工程图纸智能审查平台 v1.0 - 启动脚本
# ═══════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"

echo "═══════════════════════════════════════════════════"
echo "  工程图纸智能审查平台 v1.0"
echo "═══════════════════════════════════════════════════"

# 检查 Python
if ! command -v "$PYTHON" &>/dev/null; then
    echo "[错误] 未找到 Python，请先安装 Python 3.10+"
    exit 1
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[信息] Python 版本: $PY_VERSION"

# 虚拟环境
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo "[信息] 创建虚拟环境..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# 安装依赖
echo "[信息] 检查依赖..."
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet 2>/dev/null

# 启动
echo ""
echo "[成功] 服务已启动"
echo "[信息] 浏览器打开: http://localhost:8000"
echo "[信息] 按 Ctrl+C 停止"
echo ""

exec "$VENV_DIR/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

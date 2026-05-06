#!/bin/bash

# ================================================
#   Human-API 启动脚本 (Linux/Mac)
# ================================================

set -e

echo "================================================"
echo "  Human-API 启动脚本 (Linux/Mac)"
echo "================================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "[错误] 未检测到 Python，请先安装 Python 3.8+"
        echo "下载链接: https://www.python.org/downloads/"
        exit 1
    fi
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

echo "[1/3] 检查 Python 版本..."
$PYTHON_CMD --version
echo ""

echo "[2/3] 检查并安装依赖..."
$PYTHON_CMD -m pip install -r requirements.txt
echo "       依赖已就绪"
echo ""

echo "[3/3] 启动服务..."
echo ""
$PYTHON_CMD app.py

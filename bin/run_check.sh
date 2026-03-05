#!/bin/bash
# 云文档监控 - MCP 服务启动脚本

cd "$(dirname "$0")/.."

# 加载环境变量
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 激活虚拟环境
source venv/bin/activate

# 启动 MCP 服务（Streamable HTTP）
python -m src.mcp_server --http

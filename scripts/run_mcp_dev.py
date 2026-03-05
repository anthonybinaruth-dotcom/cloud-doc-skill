#!/usr/bin/env python3
"""MCP 服务器本地调试启动脚本"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入并获取 mcp 实例
from src.mcp_server import mcp

if __name__ == "__main__":
    # 直接运行 MCP 服务器
    if "--http" in sys.argv:
        print("启动 HTTP 模式 (http://localhost:8080)")
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
    else:
        print("启动 STDIO 模式")
        mcp.run()
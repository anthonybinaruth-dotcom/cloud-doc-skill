"""MCP服务器模块 - 云文档监控助手"""

from fastmcp import FastMCP

from .mcp_services import AppServices
from .mcp_tools_changes import register_change_tools
from .mcp_tools_compare import register_compare_tools
from .mcp_tools_docs import register_doc_tools
from .mcp_tools_misc import register_misc_tools

mcp = FastMCP("cloud-doc-monitor")
_services = AppServices()

register_doc_tools(mcp, _services)
register_change_tools(mcp, _services)
register_compare_tools(mcp, _services)
register_misc_tools(mcp, _services)


def main():
    """入口函数，供 pyproject.toml 的 console_scripts 调用"""
    import sys

    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
    else:
        mcp.run()


if __name__ == "__main__":
    main()

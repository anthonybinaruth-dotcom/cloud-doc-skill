"""MCP 其他工具。"""

from __future__ import annotations

from typing import Any, Dict

from fastmcp import FastMCP

from .mcp_services import AppServices
from .models import ChangeType, Document, DocumentChange
from .storage import ChangeDB, DocumentDB, ScanRecordDB
from .utils import compute_content_hash


def register_misc_tools(mcp: FastMCP, services: AppServices) -> None:
    @mcp.tool()
    def summarize_doc_diff(title: str, old_content: str, new_content: str) -> str:
        """对比新旧文档内容，生成AI变更摘要。"""
        try:
            import difflib

            diff = "\n".join(
                difflib.unified_diff(
                    old_content.splitlines(),
                    new_content.splitlines(),
                    fromfile="旧版本",
                    tofile="新版本",
                    lineterm="",
                )
            )

            if not diff:
                return "内容无变化"

            doc = Document(
                url="",
                title=title,
                content=new_content,
                content_hash=compute_content_hash(new_content),
            )
            change = DocumentChange(
                document=doc,
                old_content_hash=compute_content_hash(old_content),
                new_content_hash=doc.content_hash,
                diff=diff,
                change_type=ChangeType.MAJOR,
            )

            summarizer = services.get_summarizer()
            return summarizer.summarize_change(change)
        except Exception as e:
            return f"摘要生成失败: {e}"

    @mcp.tool()
    def get_statistics() -> Dict[str, Any]:
        """获取监控统计信息。"""
        try:
            storage = services.get_storage()
            session = storage.get_session()
            try:
                return {
                    "total_documents": session.query(DocumentDB).count(),
                    "total_scans": session.query(ScanRecordDB).count(),
                    "total_changes": session.query(ChangeDB).count(),
                }
            finally:
                session.close()
        except Exception as e:
            return {"error": str(e)}


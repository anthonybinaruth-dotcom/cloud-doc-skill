"""MCP服务器模块 - 阿里云文档监控助手"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import get_config
from .crawler import DocumentCrawler, url_to_alias
from .detector import ChangeDetector
from .storage import DocumentStorage, ScanRecordDB, ChangeDB, DocumentDB
from .summarizer import AISummarizer, DashScopeAdapter

mcp = FastMCP("aliyun-doc-monitor")

_storage: Optional[DocumentStorage] = None
_config = None
_summarizer = None


def _get_config():
    global _config
    if _config is None:
        _config = get_config()
    return _config


def _get_storage() -> DocumentStorage:
    global _storage
    if _storage is None:
        config = _get_config()
        db_path = config.get("storage.database", "./data/aliyun_docs.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _storage = DocumentStorage(f"sqlite:///{db_path}")
        _storage.init_db()
    return _storage


def _get_summarizer() -> AISummarizer:
    global _summarizer
    if _summarizer is None:
        config = _get_config()
        adapter = DashScopeAdapter(
            model=config.get("llm.model", "qwen-turbo"),
            api_key=config.get("llm.api_key", ""),
        )
        _summarizer = AISummarizer(adapter)
    return _summarizer


@mcp.tool()
def get_doc(url: str) -> str:
    """获取阿里云文档内容。传入文档页面URL或alias路径。

    Args:
        url: 文档URL（如 https://help.aliyun.com/zh/ecs/user-guide/what-is-ecs）
             或 alias（如 /ecs/user-guide/what-is-ecs）
    """
    try:
        crawler = DocumentCrawler(request_delay=0.5)
        doc = crawler.crawl_page(url)
        return f"标题: {doc.title}\nURL: {doc.url}\n\n{doc.content[:3000]}"
    except Exception as e:
        return f"获取失败: {e}"


@mcp.tool()
def list_product_docs(product_alias: str) -> List[Dict[str, str]]:
    """列出某个阿里云产品下的所有文档。

    Args:
        product_alias: 产品下任意文档的alias路径，如 /ecs/user-guide/what-is-ecs
    """
    try:
        crawler = DocumentCrawler(request_delay=0.5)
        menu_data = crawler.fetch_menu(product_alias)
        if menu_data is None:
            return [{"error": "无法获取产品目录"}]

        aliases = crawler.extract_aliases_from_menu(menu_data)
        return [
            {"alias": a, "url": f"https://help.aliyun.com/zh{a}"}
            for a in aliases
        ]
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def check_doc_changes(aliases: List[str]) -> str:
    """检查指定文档是否有更新，并生成变更摘要。

    Args:
        aliases: 要检查的文档alias列表，如 ["/ecs/user-guide/what-is-ecs", "/ecs/user-guide/limitations"]
    """
    try:
        config = _get_config()
        storage = _get_storage()
        crawler = DocumentCrawler(
            request_delay=config.get("crawler.request_delay", 1.0),
        )
        detector = ChangeDetector()

        old_docs = storage.get_all_documents()
        new_docs = crawler.crawl_aliases(aliases)

        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        report = detector.detect_changes(old_docs, new_docs)

        # 生成摘要
        results = []
        summarizer = _get_summarizer()

        if report.added:
            results.append(f"新增 {len(report.added)} 个文档:")
            for change in report.added:
                results.append(f"  + {change.document.title} ({change.document.url})")

        if report.modified:
            results.append(f"\n修改 {len(report.modified)} 个文档:")
            for change in report.modified:
                summary = summarizer.summarize_change(change)
                results.append(f"  ~ {change.document.title}")
                results.append(f"    摘要: {summary}")

        if report.deleted:
            results.append(f"\n删除 {len(report.deleted)} 个文档:")
            for change in report.deleted:
                results.append(f"  - {change.document.title}")

        if not results:
            return "未检测到变更"

        return "\n".join(results)
    except Exception as e:
        return f"检查失败: {e}"


@mcp.tool()
def check_product_changes(product_alias: str, max_pages: int = 50) -> str:
    """检查整个产品的文档更新，自动发现所有文档并检测变更。

    Args:
        product_alias: 产品下任意文档的alias，如 /ecs/user-guide/what-is-ecs
        max_pages: 最大检查文档数，默认50
    """
    try:
        crawler = DocumentCrawler(request_delay=1.0)
        aliases = crawler.discover_product_docs(product_alias)

        if not aliases:
            return f"未找到产品文档: {product_alias}"

        if max_pages and len(aliases) > max_pages:
            aliases = aliases[:max_pages]

        return check_doc_changes(aliases)
    except Exception as e:
        return f"检查失败: {e}"


@mcp.tool()
def summarize_doc_diff(title: str, old_content: str, new_content: str) -> str:
    """对比新旧文档内容，生成AI变更摘要。

    Args:
        title: 文档标题
        old_content: 旧版本内容
        new_content: 新版本内容
    """
    try:
        import difflib
        from .models import Document, DocumentChange, ChangeType
        from .utils import compute_content_hash

        diff = "\n".join(difflib.unified_diff(
            old_content.splitlines(), new_content.splitlines(),
            fromfile="旧版本", tofile="新版本", lineterm=""
        ))

        if not diff:
            return "内容无变化"

        doc = Document(
            url="", title=title, content=new_content,
            content_hash=compute_content_hash(new_content),
        )
        change = DocumentChange(
            document=doc,
            old_content_hash=compute_content_hash(old_content),
            new_content_hash=doc.content_hash,
            diff=diff,
            change_type=ChangeType.MAJOR,
        )

        summarizer = _get_summarizer()
        return summarizer.summarize_change(change)
    except Exception as e:
        return f"摘要生成失败: {e}"


@mcp.tool()
def get_statistics() -> Dict[str, Any]:
    """获取监控统计信息"""
    try:
        storage = _get_storage()
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


if __name__ == "__main__":
    import sys
    # 支持 SSE 模式：python -m src.mcp_server --sse
    if "--sse" in sys.argv:
        mcp.run(transport="sse", host="0.0.0.0", port=8080)
    else:
        mcp.run()

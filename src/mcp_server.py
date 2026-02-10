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
    return _check_doc_changes_impl(aliases)


def _check_doc_changes_impl(aliases: List[str]) -> str:
    """check_doc_changes 的核心实现"""
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
            for doc in report.added:
                results.append(f"  + {doc.title} ({doc.url})")

        if report.modified:
            results.append(f"\n修改 {len(report.modified)} 个文档:")
            for change in report.modified:
                summary = summarizer.summarize_change(change)
                results.append(f"  ~ {change.document.title}")
                results.append(f"    摘要: {summary}")

        if report.deleted:
            results.append(f"\n删除 {len(report.deleted)} 个文档:")
            for doc in report.deleted:
                results.append(f"  - {doc.title}")

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

        return _check_doc_changes_impl(aliases)
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
def check_recent_updates(product_alias: str, days: int = 7, max_pages: int = 200) -> str:
    """检查某个产品在最近N天内更新过的文档，对比旧版本内容差异，并用AI总结具体更新了什么。

    Args:
        product_alias: 产品alias，如 /oss、/ecs、/rds
        days: 查看最近几天的更新，默认7天
        max_pages: 最大检查文档数，默认200
    """
    try:
        import difflib
        from .models import Document, DocumentChange, ChangeType
        from .utils import compute_content_hash

        crawler = DocumentCrawler(request_delay=0.3)
        storage = _get_storage()
        aliases = crawler.discover_product_docs(product_alias)

        if not aliases:
            return f"未找到产品文档: {product_alias}"

        if max_pages and len(aliases) > max_pages:
            aliases = aliases[:max_pages]

        cutoff = datetime.now() - timedelta(days=days)
        updated_docs = []

        # 第一步：按 lastModifiedTime 筛选出最近更新的文档
        for alias in aliases:
            try:
                data = crawler.fetch_doc_by_alias(alias)
                if data is None:
                    continue
                last_modified_ms = data.get("lastModifiedTime")
                if last_modified_ms:
                    last_modified = datetime.fromtimestamp(last_modified_ms / 1000)
                    if last_modified >= cutoff:
                        doc = crawler.parse_api_response(data, alias)
                        updated_docs.append({
                            "doc": doc,
                            "last_modified": last_modified.strftime("%Y-%m-%d %H:%M"),
                        })
            except Exception as e:
                logging.warning(f"检查文档失败 {alias}: {e}")

        if not updated_docs:
            return f"最近 {days} 天内没有文档更新"

        # 第二步：对比旧版本，生成 diff 和 AI 摘要
        results = [f"最近 {days} 天内更新了 {len(updated_docs)} 个文档:\n"]
        summarizer = _get_summarizer()
        change_summaries = []

        for item in updated_docs:
            doc = item["doc"]
            results.append(f"  - {doc.title} (更新时间: {item['last_modified']})")
            results.append(f"    链接: {doc.url}")

            # 从数据库获取旧版本
            old_doc = storage.get_document(doc.url)

            if old_doc and old_doc.content and old_doc.content_hash != doc.content_hash:
                # 有旧版本且内容不同，生成 diff
                diff = "\n".join(difflib.unified_diff(
                    old_doc.content.splitlines(), doc.content.splitlines(),
                    fromfile="旧版本", tofile="新版本", lineterm=""
                ))
                if diff:
                    change = DocumentChange(
                        document=doc,
                        old_content_hash=old_doc.content_hash,
                        new_content_hash=doc.content_hash,
                        diff=diff,
                        change_type=ChangeType.MAJOR,
                    )
                    try:
                        summary = summarizer.summarize_change(change)
                        results.append(f"    变更摘要: {summary}")
                        change_summaries.append(f"《{doc.title}》: {summary}")
                    except Exception as e:
                        results.append(f"    变更摘要生成失败: {e}")
            else:
                results.append(f"    (新文档或无历史版本可对比)")

            # 保存新版本到数据库
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        # 第三步：AI 总结所有变更
        if change_summaries:
            try:
                all_summaries = "\n\n".join(change_summaries)
                prompt = (
                    f"以下是阿里云产品最近{days}天内各文档的具体变更摘要，"
                    f"请综合总结这些更新的重点内容和影响:\n\n{all_summaries}"
                )
                overall = summarizer.llm.generate(prompt)
                results.append(f"\n--- 综合总结 ---\n{overall}")
            except Exception as e:
                logging.warning(f"综合总结失败: {e}")

        return "\n".join(results)
    except Exception as e:
        return f"检查失败: {e}"


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


def main():
    """入口函数，供 pyproject.toml 的 console_scripts 调用"""
    import sys
    if "--sse" in sys.argv:
        mcp.run(transport="sse", host="0.0.0.0", port=8080)
    else:
        mcp.run()


if __name__ == "__main__":
    main()

"""MCP 变更检测与监控工具。"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta
from typing import List

from fastmcp import Context, FastMCP

from .crawler import DocumentCrawler
from .detector import ChangeDetector
from .mcp_services import AppServices
from .models import Document
from .tencent_crawler import TencentDocCrawler
from .utils import compute_content_hash


async def _check_doc_changes_impl(
    services: AppServices,
    aliases: List[str],
    ctx: Context,
    include_deleted: bool = True,
) -> str:
    """阿里云 check_doc_changes 核心实现。"""
    try:
        config = services.get_config()
        storage = services.get_storage()
        crawler = DocumentCrawler(
            request_delay=config.get("crawler.request_delay", 1.0),
        )
        detector = ChangeDetector()

        await ctx.report_progress(0, len(aliases) + 2, "获取历史文档...")
        old_docs_all = storage.get_all_documents()
        scope_urls = services.build_scope_urls(aliases)
        old_docs = [doc for doc in old_docs_all if doc.url in scope_urls]

        new_docs = []
        failed_aliases = []
        for i, alias in enumerate(aliases):
            await ctx.report_progress(i + 1, len(aliases) + 2, f"爬取 {i+1}/{len(aliases)}: {alias}")
            try:
                doc = crawler.crawl_page(alias)
                new_docs.append(doc)
            except Exception as e:
                logging.error(f"获取失败 {alias}: {e}")
                failed_aliases.append(alias)

        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        await ctx.report_progress(len(aliases) + 1, len(aliases) + 2, "检测变更...")
        report = detector.detect_changes(old_docs, new_docs)
        skipped_deleted_reason = ""
        if not include_deleted:
            report.deleted = []
            skipped_deleted_reason = "本次为定向文档检查，已跳过删除判定。"
        elif failed_aliases and report.deleted:
            report.deleted = []
            skipped_deleted_reason = f"本次有 {len(failed_aliases)} 篇文档抓取失败，为避免误报已跳过删除判定。"

        results = []
        summarizer = services.get_summarizer()

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
        elif skipped_deleted_reason:
            results.append(f"\n删除判定: {skipped_deleted_reason}")

        if not results:
            return "未检测到变更"

        await ctx.report_progress(len(aliases) + 2, len(aliases) + 2)
        return "\n".join(results)
    except Exception as e:
        return f"检查失败: {e}"


async def _check_tencent_doc_changes_impl(
    services: AppServices,
    doc_refs: List[str],
    ctx: Context,
    include_deleted: bool = True,
    default_product_id: str = "",
) -> str:
    """腾讯云 check_doc_changes 核心实现。"""
    try:
        config = services.get_config()
        storage = services.get_storage()
        crawler = TencentDocCrawler(
            request_delay=config.get("crawler.request_delay", 1.0),
        )
        detector = ChangeDetector()
        normalized_pid = services.extract_digits(default_product_id)

        await ctx.report_progress(0, len(doc_refs) + 2, "获取历史文档...")
        old_docs_all = storage.get_all_documents()
        scope_urls = services.build_tencent_scope_urls(doc_refs, normalized_pid)
        old_docs = [doc for doc in old_docs_all if doc.url in scope_urls]

        new_docs = []
        failed_refs = []
        for i, doc_ref in enumerate(doc_refs):
            await ctx.report_progress(i + 1, len(doc_refs) + 2, f"爬取 {i+1}/{len(doc_refs)}: {doc_ref}")
            product_id, doc_id = services.parse_tencent_doc_ref(doc_ref, normalized_pid)
            if not doc_id:
                failed_refs.append(doc_ref)
                continue

            try:
                doc_detail = crawler.fetch_doc(doc_id=doc_id, product_id=product_id)
                if not doc_detail:
                    failed_refs.append(doc_ref)
                    continue
                doc_model = services.build_tencent_document(doc_detail)
                if not doc_model:
                    failed_refs.append(doc_ref)
                    continue

                new_docs.append(doc_model)
                scope_urls.add(doc_model.url)
            except Exception as e:
                logging.error(f"获取失败 {doc_ref}: {e}")
                failed_refs.append(doc_ref)

        if scope_urls:
            old_docs = [doc for doc in old_docs_all if doc.url in scope_urls]

        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        await ctx.report_progress(len(doc_refs) + 1, len(doc_refs) + 2, "检测变更...")
        report = detector.detect_changes(old_docs, new_docs)
        skipped_deleted_reason = ""
        if not include_deleted:
            report.deleted = []
            skipped_deleted_reason = "本次为定向文档检查，已跳过删除判定。"
        elif failed_refs and report.deleted:
            report.deleted = []
            skipped_deleted_reason = f"本次有 {len(failed_refs)} 篇文档抓取失败，为避免误报已跳过删除判定。"

        summarizer = services.get_summarizer()
        results = []

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
        elif skipped_deleted_reason:
            results.append(f"\n删除判定: {skipped_deleted_reason}")

        if not results:
            return "未检测到变更"

        await ctx.report_progress(len(doc_refs) + 2, len(doc_refs) + 2)
        return "\n".join(results)
    except Exception as e:
        return f"检查失败: {e}"


async def _monitor_tencent_products_impl(
    services: AppServices,
    products: List[str],
    ctx: Context,
    max_pages_per_product: int = 0,
    send_notification: bool = True,
) -> str:
    """腾讯云文档监控实现。"""
    from .notifier import NotificationManager

    try:
        config = services.get_config()
        storage = services.get_storage()
        crawler = TencentDocCrawler(request_delay=0.5)
        detector = ChangeDetector()
        summarizer = services.get_summarizer()

        await ctx.report_progress(0, len(products) + 2, "获取历史文档...")
        old_docs_all = storage.get_all_documents()

        new_docs = []
        scanned_urls: set[str] = set()
        partial_crawl_detected = False
        product_stats = []
        seen_urls = set()

        for pi, raw_product_id in enumerate(products):
            product_id = services.extract_digits(raw_product_id)
            await ctx.report_progress(pi + 1, len(products) + 2, f"检查腾讯云产品 {pi+1}/{len(products)}: {raw_product_id}")

            if not product_id:
                partial_crawl_detected = True
                product_stats.append(f"  - {raw_product_id}: 非法 product_id")
                continue

            try:
                docs_list = crawler.discover_product_docs(
                    product_id=product_id,
                    limit=max_pages_per_product if max_pages_per_product > 0 else 0,
                )
                if not docs_list:
                    product_stats.append(f"  - {product_id}: 未找到文档")
                    continue

                if max_pages_per_product > 0:
                    partial_crawl_detected = True

                fetched_count = 0
                for doc_info in docs_list:
                    base_url = str(doc_info.get("url", "")).strip()
                    if base_url:
                        scanned_urls.add(base_url)
                    doc_detail = crawler.fetch_doc(
                        doc_id=str(doc_info.get("doc_id", "")),
                        product_id=str(doc_info.get("product_id", product_id)),
                    )
                    if not doc_detail:
                        partial_crawl_detected = True
                        continue

                    doc_model = services.build_tencent_document(doc_detail)
                    if not doc_model:
                        partial_crawl_detected = True
                        continue

                    if doc_model.url in seen_urls:
                        continue
                    seen_urls.add(doc_model.url)
                    scanned_urls.add(doc_model.url)
                    fetched_count += 1
                    new_docs.append(doc_model)

                if fetched_count < len(docs_list):
                    partial_crawl_detected = True
                product_stats.append(f"  - {product_id}: 获取 {fetched_count} 篇文档")
            except Exception as e:
                partial_crawl_detected = True
                product_stats.append(f"  - {product_id}: 检查失败 ({e})")

        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        await ctx.report_progress(len(products) + 1, len(products) + 2, "检测变更...")
        old_docs = [doc for doc in old_docs_all if doc.url in scanned_urls]
        report = detector.detect_changes(old_docs, new_docs)

        deleted_skipped_reason = ""
        if max_pages_per_product > 0:
            report.deleted = []
            deleted_skipped_reason = "设置了 max_pages_per_product，当前为子集扫描，已跳过删除判定。"
        elif partial_crawl_detected and report.deleted:
            report.deleted = []
            deleted_skipped_reason = "本次存在抓取不完整情况，为避免误报已跳过删除判定。"

        results = [
            "## 产品监控结果",
            "",
            f"**云厂商**: 腾讯云",
            f"**检查产品**: {len(products)} 个",
            "\n".join(product_stats),
            "",
            "**检测结果**:",
            f"  - 新增文档: {len(report.added)}",
            f"  - 修改文档: {len(report.modified)}",
            f"  - 删除文档: {len(report.deleted)}",
        ]
        if deleted_skipped_reason:
            results.append(f"  - 删除判定说明: {deleted_skipped_reason}")

        summary = ""
        if report.modified:
            summary = summarizer.summarize_batch(report.modified)
            results.append(f"\n**变更摘要**:\n{summary}")

        total_changes = len(report.added) + len(report.modified) + len(report.deleted)
        if send_notification and total_changes > 0:
            notifier = NotificationManager(config.get_all())
            notify_results = notifier.notify_changes(report, summary or "检测到文档变更")
            results.append(f"\n**通知发送**: {notify_results}")

        await ctx.report_progress(len(products) + 2, len(products) + 2)
        return "\n".join(results)
    except Exception as e:
        logging.error(f"腾讯云产品监控失败: {e}", exc_info=True)
        return f"监控失败: {e}"


async def _check_recent_volcano_updates_impl(
    services: AppServices,
    product_name: str,
    ctx: Context,
    days: int = 7,
) -> str:
    """火山云最近 N 天更新检查实现。"""
    from .models import ChangeType, DocumentChange
    from .volcano_crawler import VolcanoDocCrawler

    try:
        crawler = VolcanoDocCrawler(request_delay=0.3)
        storage = services.get_storage()
        summarizer = services.get_summarizer()

        await ctx.report_progress(0, 3, f"发现火山云 {product_name} 文档...")
        docs_list = crawler.discover_product_docs(product_name, limit=200)
        if not docs_list:
            return f"未找到火山云产品文档: {product_name}"

        cutoff = datetime.now() - timedelta(days=days)
        total = len(docs_list)
        updated_docs = []
        seen_urls = set()

        for i, item in enumerate(docs_list):
            if i % 5 == 0:
                await ctx.report_progress(i, total, f"扫描文档 {i+1}/{total}")

            lib_id = item.get("lib_id", "")
            doc_id = item.get("doc_id", "")
            if not lib_id or not doc_id:
                continue

            doc_detail = crawler.fetch_doc(lib_id, doc_id)
            if not doc_detail:
                continue

            url = doc_detail.get("url", f"https://www.volcengine.com/docs/{lib_id}/{doc_id}")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # 火山云 API 返回的更新时间
            last_modified = services.parse_datetime_value(doc_detail.get("last_modified"))

            doc = Document(
                url=url,
                title=doc_detail.get("title", ""),
                content=doc_detail.get("text", ""),
                content_hash=compute_content_hash(doc_detail.get("text", "")),
                last_modified=last_modified,
                crawled_at=datetime.now(),
            )

            # 如果有更新时间且在时间范围内
            if last_modified and last_modified >= cutoff:
                updated_docs.append({
                    "doc": doc,
                    "last_modified": last_modified.strftime("%Y-%m-%d %H:%M"),
                })
            elif not last_modified:
                # 没有更新时间的文档，检查内容变化
                old_doc = storage.get_document(url)
                if not old_doc or old_doc.content_hash != doc.content_hash:
                    updated_docs.append({
                        "doc": doc,
                        "last_modified": "未知",
                    })

        if not updated_docs:
            return f"已检查 {total} 篇文档，最近 {days} 天内没有更新"

        await ctx.report_progress(total, total + 1, "生成变更摘要...")

        results = [f"已检查 {total} 篇，最近 {days} 天内更新了 {len(updated_docs)} 个文档:\n"]
        change_summaries = []

        for item in updated_docs:
            doc = item["doc"]
            results.append(f"  - {doc.title} (更新时间: {item['last_modified']})")
            results.append(f"    链接: {doc.url}")

            old_doc = storage.get_document(doc.url)
            if old_doc and old_doc.content and old_doc.content_hash != doc.content_hash:
                diff = "\n".join(
                    difflib.unified_diff(
                        old_doc.content.splitlines(),
                        doc.content.splitlines(),
                        fromfile="旧版本",
                        tofile="新版本",
                        lineterm="",
                    )
                )
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
                results.append("    (新文档或无历史版本可对比)")

            doc_id_db = storage.save_document(doc)
            storage.save_version(doc_id_db, doc.content, doc.content_hash)

        if change_summaries:
            try:
                all_summaries = "\n\n".join(change_summaries)
                prompt = (
                    f"以下是火山云产品最近{days}天内各文档的具体变更摘要，"
                    f"请综合总结这些更新的重点内容和影响:\n\n{all_summaries}"
                )
                overall = summarizer.llm.generate(prompt)
                results.append(f"\n--- 综合总结 ---\n{overall}")
            except Exception as e:
                logging.warning(f"综合总结失败: {e}")

        await ctx.report_progress(total + 1, total + 1)
        return "\n".join(results)
    except Exception as e:
        logging.error(f"火山云更新检查失败: {e}", exc_info=True)
        return f"检查失败: {e}"


async def _check_recent_tencent_updates_impl(
    services: AppServices,
    product_id: str,
    ctx: Context,
    days: int = 7,
) -> str:
    """腾讯云最近 N 天更新检查实现。"""
    from .models import ChangeType, DocumentChange

    try:
        crawler = TencentDocCrawler(request_delay=0.3)
        storage = services.get_storage()
        summarizer = services.get_summarizer()

        await ctx.report_progress(0, 3, "发现腾讯云产品文档...")
        docs_list = crawler.discover_product_docs(product_id)
        if not docs_list:
            return f"未找到腾讯云产品文档: {product_id}"

        cutoff = datetime.now() - timedelta(days=days)
        total = len(docs_list)
        updated_docs = []
        seen_urls = set()

        for i, item in enumerate(docs_list):
            if i % 5 == 0:
                await ctx.report_progress(i, total, f"扫描文档 {i+1}/{total}")

            doc_detail = crawler.fetch_doc(
                doc_id=str(item.get("doc_id", "")),
                product_id=str(item.get("product_id", product_id)),
            )
            if not doc_detail:
                continue

            doc = services.build_tencent_document(doc_detail)
            if not doc or doc.url in seen_urls:
                continue
            seen_urls.add(doc.url)

            last_modified = doc.last_modified or services.parse_datetime_value(doc_detail.get("last_modified"))
            if last_modified and last_modified >= cutoff:
                updated_docs.append(
                    {
                        "doc": doc,
                        "last_modified": last_modified.strftime("%Y-%m-%d %H:%M"),
                    }
                )

        if not updated_docs:
            return f"已检查 {total} 篇文档，最近 {days} 天内没有更新"

        await ctx.report_progress(total, total + 1, "生成变更摘要...")

        results = [f"已检查 {total} 篇，最近 {days} 天内更新了 {len(updated_docs)} 个文档:\n"]
        change_summaries = []

        for item in updated_docs:
            doc = item["doc"]
            results.append(f"  - {doc.title} (更新时间: {item['last_modified']})")
            results.append(f"    链接: {doc.url}")

            old_doc = storage.get_document(doc.url)
            if old_doc and old_doc.content and old_doc.content_hash != doc.content_hash:
                diff = "\n".join(
                    difflib.unified_diff(
                        old_doc.content.splitlines(),
                        doc.content.splitlines(),
                        fromfile="旧版本",
                        tofile="新版本",
                        lineterm="",
                    )
                )
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
                results.append("    (新文档或无历史版本可对比)")

            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        if change_summaries:
            try:
                all_summaries = "\n\n".join(change_summaries)
                prompt = (
                    f"以下是腾讯云产品最近{days}天内各文档的具体变更摘要，"
                    f"请综合总结这些更新的重点内容和影响:\n\n{all_summaries}"
                )
                overall = summarizer.llm.generate(prompt)
                results.append(f"\n--- 综合总结 ---\n{overall}")
            except Exception as e:
                logging.warning(f"综合总结失败: {e}")

        await ctx.report_progress(total + 1, total + 1)
        return "\n".join(results)
    except Exception as e:
        return f"检查失败: {e}"


def register_change_tools(mcp: FastMCP, services: AppServices) -> None:
    @mcp.tool()
    async def check_doc_changes(
        aliases: list[str],
        ctx: Context,
        cloud: str = "aliyun",
        tencent_product_id: str = "",
    ) -> str:
        """检查指定文档是否有更新，并生成变更摘要。"""
        cloud_norm = (cloud or "aliyun").strip().lower()
        if cloud_norm == "tencent":
            return await _check_tencent_doc_changes_impl(
                services=services,
                doc_refs=aliases,
                ctx=ctx,
                include_deleted=False,
                default_product_id=tencent_product_id,
            )
        if cloud_norm != "aliyun":
            return f"不支持的 cloud: {cloud}，当前仅支持 aliyun 或 tencent"
        return await _check_doc_changes_impl(services, aliases, ctx, include_deleted=False)

    @mcp.tool()
    async def check_product_changes(
        product_alias: str,
        ctx: Context,
        max_pages: int = 0,
        cloud: str = "aliyun",
        tencent_product_id: str = "",
    ) -> str:
        """检查整个产品的文档更新，自动发现所有文档并检测变更。"""
        cloud_norm = (cloud or "aliyun").strip().lower()
        if cloud_norm == "tencent":
            try:
                product_id = services.extract_digits(tencent_product_id or product_alias)
                if not product_id:
                    return f"无效的腾讯云 product_id: {tencent_product_id or product_alias}"

                await ctx.report_progress(0, 1, "发现产品文档...")
                crawler = TencentDocCrawler(request_delay=1.0)
                docs_list = crawler.discover_product_docs(product_id)
                if not docs_list:
                    return f"未找到腾讯云产品文档: {product_id}"

                doc_refs = [str(item.get("url") or item.get("doc_id") or "") for item in docs_list]
                if max_pages > 0 and len(doc_refs) > max_pages:
                    doc_refs = doc_refs[:max_pages]

                include_deleted = max_pages <= 0
                return await _check_tencent_doc_changes_impl(
                    services=services,
                    doc_refs=doc_refs,
                    ctx=ctx,
                    include_deleted=include_deleted,
                    default_product_id=product_id,
                )
            except Exception as e:
                return f"检查失败: {e}"

        if cloud_norm != "aliyun":
            return f"不支持的 cloud: {cloud}，当前仅支持 aliyun 或 tencent"

        try:
            await ctx.report_progress(0, 1, "发现产品文档...")
            crawler = DocumentCrawler(request_delay=1.0)
            aliases = crawler.discover_product_docs(product_alias)

            if not aliases:
                return f"未找到产品文档: {product_alias}"

            if max_pages > 0 and len(aliases) > max_pages:
                aliases = aliases[:max_pages]

            include_deleted = max_pages <= 0
            return await _check_doc_changes_impl(services, aliases, ctx, include_deleted=include_deleted)
        except Exception as e:
            return f"检查失败: {e}"

    @mcp.tool()
    async def monitor_products(
        products: list[str],
        ctx: Context,
        max_pages_per_product: int = 0,
        send_notification: bool = True,
        cloud: str = "aliyun",
    ) -> str:
        """监控指定云厂商产品列表的文档变更，并可选发送通知。"""
        from .notifier import NotificationManager

        cloud_norm = (cloud or "aliyun").strip().lower()
        if cloud_norm == "tencent":
            return await _monitor_tencent_products_impl(
                services=services,
                products=products,
                ctx=ctx,
                max_pages_per_product=max_pages_per_product,
                send_notification=send_notification,
            )
        if cloud_norm != "aliyun":
            return f"不支持的 cloud: {cloud}，当前仅支持 aliyun 或 tencent"

        try:
            config = services.get_config()
            storage = services.get_storage()
            crawler = DocumentCrawler(request_delay=0.5)
            detector = ChangeDetector()
            summarizer = services.get_summarizer()

            await ctx.report_progress(0, len(products) + 2, "获取历史文档...")
            old_docs_all = storage.get_all_documents()

            new_docs = []
            scanned_aliases = []
            partial_crawl_detected = False
            product_stats = []

            for pi, product_alias in enumerate(products):
                await ctx.report_progress(pi + 1, len(products) + 2, f"检查产品 {pi+1}/{len(products)}: {product_alias}")
                logging.info(f"正在检查产品: {product_alias}")
                try:
                    aliases = crawler.discover_product_docs(product_alias)
                    if not aliases:
                        product_stats.append(f"  - {product_alias}: 未找到文档")
                        continue

                    if max_pages_per_product > 0 and len(aliases) > max_pages_per_product:
                        aliases = aliases[:max_pages_per_product]
                        partial_crawl_detected = True

                    scanned_aliases.extend(aliases)
                    product_docs = crawler.crawl_aliases(aliases)
                    new_docs.extend(product_docs)
                    if len(product_docs) < len(aliases):
                        partial_crawl_detected = True
                    product_stats.append(f"  - {product_alias}: 获取 {len(product_docs)} 篇文档")
                except Exception as e:
                    partial_crawl_detected = True
                    product_stats.append(f"  - {product_alias}: 检查失败 ({e})")

            for doc in new_docs:
                doc_id = storage.save_document(doc)
                storage.save_version(doc_id, doc.content, doc.content_hash)

            await ctx.report_progress(len(products) + 1, len(products) + 2, "检测变更...")
            scope_urls = services.build_scope_urls(scanned_aliases)
            old_docs = [doc for doc in old_docs_all if doc.url in scope_urls]
            report = detector.detect_changes(old_docs, new_docs)
            deleted_skipped_reason = ""
            if max_pages_per_product > 0:
                report.deleted = []
                deleted_skipped_reason = "设置了 max_pages_per_product，当前为子集扫描，已跳过删除判定。"
            elif partial_crawl_detected and report.deleted:
                report.deleted = []
                deleted_skipped_reason = "本次存在抓取不完整情况，为避免误报已跳过删除判定。"

            results = [
                "## 产品监控结果",
                "",
                f"**云厂商**: 阿里云",
                f"**检查产品**: {len(products)} 个",
                "\n".join(product_stats),
                "",
                "**检测结果**:",
                f"  - 新增文档: {len(report.added)}",
                f"  - 修改文档: {len(report.modified)}",
                f"  - 删除文档: {len(report.deleted)}",
            ]
            if deleted_skipped_reason:
                results.append(f"  - 删除判定说明: {deleted_skipped_reason}")

            summary = ""
            if report.modified:
                summary = summarizer.summarize_batch(report.modified)
                results.append(f"\n**变更摘要**:\n{summary}")

            total_changes = len(report.added) + len(report.modified) + len(report.deleted)
            if send_notification and total_changes > 0:
                notifier = NotificationManager(config.get_all())
                notify_results = notifier.notify_changes(report, summary or "检测到文档变更")
                results.append(f"\n**通知发送**: {notify_results}")

            await ctx.report_progress(len(products) + 2, len(products) + 2)
            return "\n".join(results)
        except Exception as e:
            logging.error(f"产品监控失败: {e}", exc_info=True)
            return f"监控失败: {e}"

    @mcp.tool()
    async def check_recent_updates(
        product_alias: str,
        ctx: Context,
        days: int = 7,
        cloud: str = "aliyun",
        tencent_product_id: str = "",
    ) -> str:
        """检查某个产品在最近N天内更新过的文档，并生成摘要。
        
        Args:
            product_alias: 产品标识。阿里云为 alias（如 /vpc），腾讯云为产品名或ID，火山云为产品名（如"私有网络"）
            days: 检查最近多少天的更新
            cloud: 云厂商：aliyun, tencent, volcano
            tencent_product_id: 腾讯云 product_id（可选）
        """
        from .models import ChangeType, DocumentChange

        cloud_norm = (cloud or "aliyun").strip().lower()
        
        if cloud_norm == "volcano":
            product_name = product_alias.strip()
            if not product_name:
                return "请提供火山云产品名称（如：私有网络、云企业网）"
            return await _check_recent_volcano_updates_impl(services, product_name, ctx, days)
        
        if cloud_norm == "tencent":
            # 支持产品名称或数字 ID（discover_product_docs 会自动转换）
            product_ref = (tencent_product_id or product_alias).strip()
            if not product_ref:
                return "请提供腾讯云产品名称或 product_id"
            return await _check_recent_tencent_updates_impl(services, product_ref, ctx, days)

        if cloud_norm != "aliyun":
            return f"不支持的 cloud: {cloud}，当前仅支持 aliyun, tencent, volcano"

        try:
            crawler = DocumentCrawler(request_delay=0.3)
            storage = services.get_storage()

            await ctx.report_progress(0, 3, "发现产品文档...")
            aliases = crawler.discover_product_docs(product_alias)

            if not aliases:
                return f"未找到产品文档: {product_alias}"

            cutoff = datetime.now() - timedelta(days=days)
            updated_docs = []
            total = len(aliases)

            for i, alias in enumerate(aliases):
                if i % 5 == 0:
                    await ctx.report_progress(i, total, f"扫描文档 {i+1}/{total}")
                try:
                    data = crawler.fetch_doc_by_alias(alias)
                    if data is None:
                        continue
                    last_modified_ms = data.get("lastModifiedTime")
                    if last_modified_ms:
                        last_modified = datetime.fromtimestamp(last_modified_ms / 1000)
                        if last_modified >= cutoff:
                            doc = crawler.parse_api_response(data, alias)
                            updated_docs.append(
                                {
                                    "doc": doc,
                                    "last_modified": last_modified.strftime("%Y-%m-%d %H:%M"),
                                }
                            )
                except Exception as e:
                    logging.warning(f"检查文档失败 {alias}: {e}")

            if not updated_docs:
                return f"已检查 {total} 篇文档，最近 {days} 天内没有更新"

            await ctx.report_progress(total, total + 1, "生成变更摘要...")

            results = [f"已检查 {total} 篇，最近 {days} 天内更新了 {len(updated_docs)} 个文档:\n"]
            summarizer = services.get_summarizer()
            change_summaries = []

            for item in updated_docs:
                doc = item["doc"]
                results.append(f"  - {doc.title} (更新时间: {item['last_modified']})")
                results.append(f"    链接: {doc.url}")

                old_doc = storage.get_document(doc.url)
                if old_doc and old_doc.content and old_doc.content_hash != doc.content_hash:
                    diff = "\n".join(
                        difflib.unified_diff(
                            old_doc.content.splitlines(),
                            doc.content.splitlines(),
                            fromfile="旧版本",
                            tofile="新版本",
                            lineterm="",
                        )
                    )
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
                    results.append("    (新文档或无历史版本可对比)")

                doc_id = storage.save_document(doc)
                storage.save_version(doc_id, doc.content, doc.content_hash)

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

            await ctx.report_progress(total + 1, total + 1)
            return "\n".join(results)
        except Exception as e:
            return f"检查失败: {e}"


"""MCP服务器模块 - 云文档监控助手"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .config import get_config
from .crawler import DocumentCrawler, url_to_alias
from .baidu_crawler import BaiduDocCrawler
from .detector import ChangeDetector
from .storage import DocumentStorage, ScanRecordDB, ChangeDB, DocumentDB
from .summarizer import AISummarizer, DashScopeAdapter

mcp = FastMCP("cloud-doc-monitor")

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

    支持三种输入方式：
    1. 完整URL: https://help.aliyun.com/zh/ecs/user-guide/what-is-ecs
    2. alias路径: /ecs/user-guide/what-is-ecs
    3. 关键词搜索: "ecs 安全组" 或 "vpc 路由表" — 自动从产品目录中查找匹配文档

    Args:
        url: 文档URL、alias路径，或 "产品名 关键词" 格式的搜索词
    """
    try:
        crawler = DocumentCrawler(request_delay=0.5)

        # 判断是否为精确路径（URL 或 alias）
        is_url = url.startswith("http")
        is_alias = url.startswith("/") and " " not in url

        if is_url or is_alias:
            try:
                doc = crawler.crawl_page(url)
                return f"标题: {doc.title}\nURL: {doc.url}\n\n{doc.content[:3000]}"
            except Exception:
                # 精确路径失败，尝试当作关键词搜索
                pass

        # 关键词搜索模式
        return _search_and_get_doc(crawler, url)
    except Exception as e:
        return f"获取失败: {e}"


def _search_and_get_doc(crawler: DocumentCrawler, query: str) -> str:
    """用 AI 理解用户查询意图，自动识别产品并从文档目录中找到最相关的文档"""
    summarizer = _get_summarizer()

    # 第一步：让 AI 从用户查询中提取产品 alias
    product_prompt = (
        f"用户想查找的阿里云文档：「{query}」\n\n"
        f"请判断这个查询对应阿里云哪个产品，返回该产品的 alias 路径前缀。\n"
        f"常见产品 alias 示例：/ecs、/vpc、/oss、/rds、/slb、/cdn、/ram、/redis、/nas、/ack、/dns、/arms、/fc 等。\n"
        f"只输出一个 alias 路径，如 /ecs，不要输出其他内容。"
    )

    try:
        product_alias = summarizer.llm.generate(product_prompt, max_tokens=20).strip()
        # 清理 AI 输出，只保留 /xxx 格式
        import re
        match = re.search(r'(/[a-z][a-z0-9_-]*)', product_alias.lower())
        product_alias = match.group(1) if match else product_alias.lower().strip()
    except Exception as e:
        return f"AI 识别产品失败: {e}"

    # 第二步：获取产品文档目录
    aliases = crawler.discover_product_docs(product_alias)
    if not aliases:
        return f"未找到产品 {product_alias} 的文档目录"

    # 第三步：把目录列表给 AI，让它选出最相关的文档
    toc_lines = [f"{i}. {alias}" for i, alias in enumerate(aliases)]
    toc_text = "\n".join(toc_lines)

    select_prompt = (
        f"用户想查找的内容：「{query}」\n\n"
        f"以下是该产品的文档目录（编号. alias路径）：\n{toc_text}\n\n"
        f"请从目录中选出与用户查询最相关的1-3篇文档。"
        f"只输出选中文档的编号，用逗号分隔，不要输出其他内容。\n"
        f"例如：5,12,23"
    )

    try:
        ai_response = summarizer.llm.generate(select_prompt, max_tokens=50)
        import re
        numbers = re.findall(r'\d+', ai_response)
        selected_indices = [int(n) for n in numbers if int(n) < len(aliases)][:3]
    except Exception as e:
        logging.warning(f"AI 选择文档失败: {e}")
        selected_indices = []

    if not selected_indices:
        return f"未找到与 '{query}' 相关的文档"

    # 第四步：获取选中文档的内容
    primary_alias = aliases[selected_indices[0]]
    doc = crawler.crawl_page(primary_alias)
    result = f"标题: {doc.title}\nURL: {doc.url}\n\n{doc.content[:3000]}"

    if len(selected_indices) > 1:
        others = []
        for idx in selected_indices[1:]:
            data = crawler.fetch_doc_by_alias(aliases[idx])
            if data and data.get("title"):
                others.append(f"  - {data['title']} (alias: {aliases[idx]})")
        if others:
            result += "\n\n--- 其他相关文档 ---\n" + "\n".join(others)

    return result


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
def monitor_products(products: List[str], max_pages_per_product: int = 0, send_notification: bool = True) -> str:
    """监控指定产品列表的文档变更，并可选发送通知。
    
    这是主要的监控入口，可以动态指定要监控的产品，无需修改配置文件。

    Args:
        products: 产品alias列表，如 ["/vpc", "/ecs", "/oss"]
        max_pages_per_product: 每个产品最大检查文档数，0表示不限制（默认不限制）
        send_notification: 是否发送通知到 aiflow/如流，默认 True
    
    Returns:
        检查结果摘要
    """
    from .notifier import NotificationManager
    from .models import ChangeReport
    
    try:
        config = _get_config()
        storage = _get_storage()
        crawler = DocumentCrawler(request_delay=0.5)
        detector = ChangeDetector()
        summarizer = _get_summarizer()
        
        # 获取旧文档
        old_docs = storage.get_all_documents()
        
        # 爬取所有产品的文档
        new_docs = []
        product_stats = []
        
        for product_alias in products:
            logging.info(f"正在检查产品: {product_alias}")
            try:
                aliases = crawler.discover_product_docs(product_alias)
                if not aliases:
                    product_stats.append(f"  - {product_alias}: 未找到文档")
                    continue
                    
                if max_pages_per_product > 0 and len(aliases) > max_pages_per_product:
                    aliases = aliases[:max_pages_per_product]
                
                product_docs = crawler.crawl_aliases(aliases)
                new_docs.extend(product_docs)
                product_stats.append(f"  - {product_alias}: 获取 {len(product_docs)} 篇文档")
            except Exception as e:
                product_stats.append(f"  - {product_alias}: 检查失败 ({e})")
        
        # 保存新文档
        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)
        
        # 检测变更
        report = detector.detect_changes(old_docs, new_docs)
        
        # 生成结果
        results = [
            f"## 产品监控结果",
            f"",
            f"**检查产品**: {len(products)} 个",
            "\n".join(product_stats),
            f"",
            f"**检测结果**:",
            f"  - 新增文档: {len(report.added)}",
            f"  - 修改文档: {len(report.modified)}",
            f"  - 删除文档: {len(report.deleted)}",
        ]
        
        # 生成摘要并发送通知
        summary = ""
        if report.modified:
            summary = summarizer.summarize_batch(report.modified)
            results.append(f"\n**变更摘要**:\n{summary}")
        
        total_changes = len(report.added) + len(report.modified) + len(report.deleted)
        
        if send_notification and total_changes > 0:
            notifier = NotificationManager(config.get_all())
            notify_results = notifier.notify_changes(report, summary or "检测到文档变更")
            results.append(f"\n**通知发送**: {notify_results}")
        
        return "\n".join(results)
        
    except Exception as e:
        logging.error(f"产品监控失败: {e}", exc_info=True)
        return f"监控失败: {e}"


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
def compare_cloud_docs(product: str = "VPC", aliyun_alias: str = "", baidu_slug: str = "") -> str:
    """对比阿里云和百度云同一产品的文档内容，用AI生成对比分析。

    支持两种模式：
    1. 指定具体文档：传入 aliyun_alias 和 baidu_slug 对比两篇具体文档
    2. 产品级对比：只传 product，自动按标题匹配同名文档进行对比

    Args:
        product: 产品名，如 VPC、ECS/BCC、OSS/BOS（默认VPC）
        aliyun_alias: 阿里云文档alias，如 /vpc/user-guide/what-is-ecs（可选）
        baidu_slug: 百度云文档slug，如 qjwvyu0at（可选）
    """
    try:
        import re
        summarizer = _get_summarizer()
        aliyun_crawler = DocumentCrawler(request_delay=0.3)
        baidu_crawler = BaiduDocCrawler(request_delay=0.3)

        # 让 AI 从用户输入中识别阿里云和百度云的产品 alias
        mapping_prompt = (
            f"用户想对比的云产品/功能：「{product}」\n\n"
            f"请判断这个查询分别对应阿里云和百度云的哪个产品。\n"
            f"阿里云产品alias格式为 /产品名（小写），如 /ecs、/vpc、/oss\n"
            f"百度云产品名格式为大写，如 BCC、VPC、BOS\n\n"
            f"如果用户查询的是某个子功能（如安全组、路由表、存储桶），请返回它所属的主产品。\n\n"
            f"请严格按以下格式输出，不要输出其他内容：\n"
            f"aliyun:/xxx\n"
            f"baidu:YYY\n"
            f"feature:具体功能名（如果是子功能的话，否则留空）"
        )

        try:
            mapping_response = summarizer.llm.generate(mapping_prompt, max_tokens=100)
            aliyun_match = re.search(r'aliyun:\s*(/[a-z][a-z0-9_-]*)', mapping_response.lower())
            baidu_match = re.search(r'baidu:\s*([a-z][a-z0-9_-]*)', mapping_response.lower())
            feature_match = re.search(r'feature:\s*(.+)', mapping_response, re.IGNORECASE)

            aliyun_product = aliyun_match.group(1) if aliyun_match else f"/{product.lower()}"
            baidu_product = (baidu_match.group(1).upper()) if baidu_match else product.upper()
            feature_name = feature_match.group(1).strip() if feature_match and feature_match.group(1).strip() else ""
        except Exception:
            aliyun_product = f"/{product.lower()}"
            baidu_product = product.upper()
            feature_name = ""

        display_name = feature_name if feature_name else f"{aliyun_product.strip('/')}/{baidu_product}"

        if aliyun_alias and baidu_slug:
            # 模式1：对比两篇具体文档的产品功能差异
            aliyun_doc = aliyun_crawler.crawl_page(aliyun_alias)
            baidu_doc = baidu_crawler.fetch_doc(baidu_product, baidu_slug)

            if not baidu_doc:
                return f"获取百度云文档失败: {baidu_slug}"

            prompt = (
                f"以下是阿里云和百度云关于同一类产品功能的文档。"
                f"请基于文档内容，对比两个云厂商在该功能上的**产品能力差异**，"
                f"而不是对比文档本身的写法差异。\n\n"
                f"## 阿里云 - {aliyun_doc.title}\n{aliyun_doc.content[:3000]}\n\n"
                f"## 百度云 - {baidu_doc['title']}\n{baidu_doc['text'][:3000]}\n\n"
                f"请输出：\n"
                f"1. 双方都支持的功能点\n"
                f"2. 阿里云独有的功能/能力\n"
                f"3. 百度云独有的功能/能力\n"
                f"4. 同一功能的参数/规格/限制差异\n"
                f"5. 总结：哪个厂商在该功能上更有优势，为什么"
            )
            comparison = summarizer.llm.generate(prompt, max_tokens=2000)

            return (
                f"## 产品功能对比\n\n"
                f"阿里云: {aliyun_doc.title} ({aliyun_doc.url})\n"
                f"百度云: {baidu_doc['title']} ({baidu_doc['url']})\n\n"
                f"{comparison}"
            )

        else:
            # 模式2：产品级对比
            aliyun_aliases = aliyun_crawler.discover_product_docs(aliyun_product)
            baidu_docs_list = baidu_crawler.discover_product_docs(baidu_product)

            if not aliyun_aliases:
                return f"未找到阿里云 {aliyun_product} 文档"
            if not baidu_docs_list:
                return f"未找到百度云 {baidu_product} 文档"

            # 如果有具体子功能，让 AI 从目录中选出相关文档
            if feature_name:
                # 阿里云：AI 选相关文档
                aliyun_toc = "\n".join(f"{i}. {a}" for i, a in enumerate(aliyun_aliases))
                select_prompt = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是阿里云产品文档目录：\n{aliyun_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp = summarizer.llm.generate(select_prompt, max_tokens=80)
                    nums = re.findall(r'\d+', resp)
                    aliyun_selected = [aliyun_aliases[int(n)] for n in nums if int(n) < len(aliyun_aliases)][:10]
                except Exception:
                    aliyun_selected = aliyun_aliases[:10]

                # 百度云：AI 选相关文档
                baidu_toc = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(baidu_docs_list))
                select_prompt2 = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是百度云产品文档目录：\n{baidu_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp2 = summarizer.llm.generate(select_prompt2, max_tokens=80)
                    nums2 = re.findall(r'\d+', resp2)
                    baidu_selected = [baidu_docs_list[int(n)] for n in nums2 if int(n) < len(baidu_docs_list)][:10]
                except Exception:
                    baidu_selected = baidu_docs_list[:10]
            else:
                aliyun_selected = aliyun_aliases[:12]
                baidu_selected = baidu_docs_list[:12]

            results = [
                f"## 阿里云 vs 百度云「{display_name}」产品功能对比\n",
                f"阿里云文档数: {len(aliyun_aliases)}",
                f"百度云文档数: {len(baidu_docs_list)}\n",
            ]

            # 获取双方选中文档的内容
            from bs4 import BeautifulSoup as BS

            aliyun_contents = []
            for alias in aliyun_selected:
                data = aliyun_crawler.fetch_doc_by_alias(alias)
                if data and data.get("title") and data.get("content"):
                    text = BS(data["content"], "lxml").get_text(separator="\n", strip=True)
                    aliyun_contents.append(f"【{data['title']}】\n{text[:1200]}")

            baidu_contents = []
            for doc_info in baidu_selected:
                slug = doc_info["slug"] if isinstance(doc_info, dict) else doc_info
                doc = baidu_crawler.fetch_doc(baidu_product, slug)
                if doc and doc.get("text"):
                    baidu_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            if not aliyun_contents or not baidu_contents:
                results.append("无法获取足够的文档内容进行对比")
                return "\n".join(results)

            aliyun_detail = "\n\n".join(aliyun_contents)
            baidu_detail = "\n\n".join(baidu_contents)

            compare_target = feature_name if feature_name else display_name
            prompt = (
                f"你是一个云产品分析师。以下是阿里云和百度云关于「{compare_target}」的文档内容。\n"
                f"请基于这些信息，全面对比两个云厂商在「{compare_target}」上的**产品功能差异**。\n"
                f"注意：要对比的是产品功能本身的差异，不是文档写法的差异。\n\n"
                f"## 阿里云文档内容\n{aliyun_detail[:5000]}\n\n"
                f"## 百度云文档内容\n{baidu_detail[:5000]}\n\n"
                f"请按以下结构输出对比结果：\n"
                f"1. **双方都支持的功能**：列出共有功能及各自的规格参数差异\n"
                f"2. **阿里云独有功能**：百度云不具备的功能和能力\n"
                f"3. **百度云独有功能**：阿里云不具备的功能和能力\n"
                f"4. **配额与限制对比**：关键限制的差异\n"
                f"5. **综合评价**：各自的优势领域和适用场景"
            )
            comparison = summarizer.llm.generate(prompt, max_tokens=3000)
            results.append(f"{comparison}")

            return "\n".join(results)

    except Exception as e:
        logging.error(f"文档对比失败: {e}", exc_info=True)
        return f"文档对比失败: {e}"


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

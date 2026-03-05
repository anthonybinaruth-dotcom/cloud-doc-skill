"""云文档监控助手 - 主入口

用法:
    python -m src.main              # 启动定时任务服务
    python -m src.main --check-now  # 立即执行一次检查
    python -m src.main --help       # 显示帮助
"""

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from .config import get_config
from .crawler import DocumentCrawler
from .baidu_crawler import BaiduDocCrawler
from .tencent_crawler import TencentDocCrawler
from .detector import ChangeDetector
from .models import ChangeType, Document
from .notifier import NotificationManager
from .scheduler import Scheduler
from .storage import DocumentStorage
from .summarizer import AISummarizer
from .utils import compute_content_hash


# 配置日志
def setup_logging(config: dict) -> None:
    """配置日志系统"""
    log_config = config.get("logging", {})
    log_level = getattr(logging, log_config.get("level", "INFO").upper())
    log_file = log_config.get("file", "./logs/monitor.log")
    
    # 确保日志目录存在
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    # 配置根日志器
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


async def check_and_notify(
    products: List[str],
    crawler: DocumentCrawler,
    storage: DocumentStorage,
    detector: ChangeDetector,
    summarizer: AISummarizer,
    notifier: NotificationManager,
    max_pages_per_product: int = 0,
) -> str:
    """执行文档检查并发送通知
    
    Returns:
        执行结果摘要
    """
    logging.info(f"开始检查 {len(products)} 个产品的文档更新...")
    start_time = datetime.now()
    
    # 获取历史文档
    old_docs_all = storage.get_all_documents()
    
    new_docs = []
    scanned_aliases = []
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
            
            scanned_aliases.extend(aliases)
            product_docs = crawler.crawl_aliases(aliases)
            new_docs.extend(product_docs)
            product_stats.append(f"  - {product_alias}: {len(product_docs)}/{len(aliases)} 篇")
        except Exception as e:
            logging.error(f"检查产品 {product_alias} 失败: {e}")
            product_stats.append(f"  - {product_alias}: 错误 - {e}")
    
    if not new_docs:
        msg = "未获取到任何文档内容"
        logging.warning(msg)
        return msg
    
    # 检测变更
    logging.info("开始检测变更...")
    changes = detector.detect_changes(old_docs_all, new_docs, scanned_aliases)
    
    # 保存新文档到数据库
    for doc in new_docs:
        try:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)
        except Exception as e:
            logging.error(f"保存文档失败 {doc.url}: {e}")
    
    # 生成结果摘要
    elapsed = (datetime.now() - start_time).total_seconds()
    
    if not changes:
        msg = f"检查完成，共 {len(new_docs)} 篇文档，无变更（耗时 {elapsed:.1f}s）"
        logging.info(msg)
        return msg
    
    # 统计变更
    added = [c for c in changes if c.change_type == ChangeType.ADDED]
    modified = [c for c in changes if c.change_type == ChangeType.MODIFIED]
    deleted = [c for c in changes if c.change_type == ChangeType.DELETED]
    
    logging.info(f"检测到 {len(changes)} 处变更: 新增 {len(added)}, 修改 {len(modified)}, 删除 {len(deleted)}")
    
    # 生成 AI 摘要
    logging.info("生成变更摘要...")
    change_summaries = []
    for change in changes[:20]:  # 限制数量避免过长
        try:
            summary = summarizer.summarize_change(change)
            change.summary = summary
            change_summaries.append(f"- {change.title}: {summary}")
        except Exception as e:
            logging.error(f"生成摘要失败 {change.title}: {e}")
            change_summaries.append(f"- {change.title}: (摘要生成失败)")
    
    # 生成总体摘要
    overall_summary = ""
    if change_summaries:
        try:
            all_summaries = "\n".join(change_summaries)
            prompt = f"以下是云文档的变更摘要，请用2-3句话概括这些变更的核心内容:\n\n{all_summaries}"
            overall_summary = summarizer.llm.generate(prompt, max_tokens=200)
        except Exception as e:
            logging.error(f"生成总体摘要失败: {e}")
    
    # 发送通知
    logging.info("发送通知...")
    try:
        notifier.send_notification(
            title="云文档监控报告",
            summary=overall_summary or f"检测到 {len(changes)} 处变更",
            changes=changes,
        )
        logging.info("通知发送成功")
    except Exception as e:
        logging.error(f"发送通知失败: {e}")
    
    # 返回结果
    result_lines = [
        f"## 检查完成 (耗时 {elapsed:.1f}s)",
        f"",
        f"### 产品统计",
        *product_stats,
        f"",
        f"### 变更统计",
        f"- 新增: {len(added_docs)} 篇",
        f"- 修改: {len(modified_changes)} 篇",
        f"- 删除: {len(deleted_docs)} 篇",
        f"",
        f"### 变更详情",
        *change_summaries[:10],
    ]
    
    if overall_summary:
        result_lines.extend([f"", f"### 总体摘要", overall_summary])
    
    return "\n".join(result_lines)


async def check_tencent_docs(
    products: List[str],
    storage: DocumentStorage,
    detector: ChangeDetector,
    summarizer: AISummarizer,
    request_delay: float = 0.5,
    max_docs: int = 50,
) -> tuple:
    """检查腾讯云文档更新"""
    crawler = TencentDocCrawler(request_delay=request_delay)
    new_docs = []
    product_stats = []
    scanned_urls = []
    
    for product_id in products:
        logging.info(f"[腾讯云] 正在检查产品: {product_id}")
        try:
            docs_list = crawler.discover_product_docs(product_id)
            if not docs_list:
                product_stats.append(f"  - 腾讯云 {product_id}: 未找到文档")
                continue
            
            fetched = 0
            docs_to_fetch = docs_list if max_docs == 0 else docs_list[:max_docs]
            for doc_info in docs_to_fetch:  # max_docs=0 表示不限制
                doc_id = doc_info.get("doc_id", "")
                pid = doc_info.get("product_id", product_id)
                if not doc_id:
                    continue
                doc_data = crawler.fetch_doc(doc_id=doc_id, product_id=pid)
                if doc_data and doc_data.get("text"):
                    url = doc_data.get("url", f"https://cloud.tencent.com/document/product/{pid}/{doc_id}")
                    scanned_urls.append(url)
                    doc = Document(
                        url=url,
                        title=doc_data.get("title", ""),
                        content=doc_data.get("text", ""),
                        content_hash=compute_content_hash(doc_data.get("text", "")),
                        crawled_at=datetime.now(),
                    )
                    new_docs.append(doc)
                    fetched += 1
            product_stats.append(f"  - 腾讯云 {product_id}: {fetched}/{len(docs_list)} 篇")
        except Exception as e:
            logging.error(f"[腾讯云] 检查产品 {product_id} 失败: {e}")
            product_stats.append(f"  - 腾讯云 {product_id}: 错误 - {e}")
    
    return new_docs, product_stats, scanned_urls


async def check_baidu_docs(
    products: List[str],
    storage: DocumentStorage,
    detector: ChangeDetector,
    summarizer: AISummarizer,
    request_delay: float = 0.5,
) -> tuple:
    """检查百度云文档更新"""
    crawler = BaiduDocCrawler(request_delay=request_delay)
    new_docs = []
    product_stats = []
    scanned_urls = []
    
    for product in products:
        logging.info(f"[百度云] 正在检查产品: {product}")
        try:
            docs_list = crawler.discover_product_docs(product)
            if not docs_list:
                product_stats.append(f"  - 百度云 {product}: 未找到文档")
                continue
            
            fetched = 0
            for doc_info in docs_list[:50]:  # 每个产品最多50篇
                slug = doc_info.get("slug", "") if isinstance(doc_info, dict) else doc_info
                if not slug:
                    continue
                doc_data = crawler.fetch_doc(product, slug)
                if doc_data and doc_data.get("text"):
                    url = doc_data.get("url", f"https://cloud.baidu.com/doc/{product}/{slug}")
                    scanned_urls.append(url)
                    doc = Document(
                        url=url,
                        title=doc_data.get("title", ""),
                        content=doc_data.get("text", ""),
                        content_hash=compute_content_hash(doc_data.get("text", "")),
                        crawled_at=datetime.now(),
                    )
                    new_docs.append(doc)
                    fetched += 1
            product_stats.append(f"  - 百度云 {product}: {fetched}/{len(docs_list)} 篇")
        except Exception as e:
            logging.error(f"[百度云] 检查产品 {product} 失败: {e}")
            product_stats.append(f"  - 百度云 {product}: 错误 - {e}")
    
    return new_docs, product_stats, scanned_urls


async def check_volcano_docs(
    products: List[str],
    storage: DocumentStorage,
    detector: ChangeDetector,
    summarizer: AISummarizer,
    request_delay: float = 0.3,
    max_docs: int = 0,
    concurrency: int = 10,
) -> tuple:
    """检查火山云文档更新（并发版本）
    
    Args:
        products: 产品名称列表，如 ["私有网络", "云企业网"]
        max_docs: 每个产品最大文档数，0 表示不限制
        concurrency: 并发数
    """
    import aiohttp
    from .volcano_crawler import VolcanoDocCrawler
    
    crawler = VolcanoDocCrawler(request_delay=request_delay)
    new_docs = []
    product_stats = []
    scanned_urls = []
    
    # 并发控制信号量
    semaphore = asyncio.Semaphore(concurrency)
    
    async def fetch_single_doc(session: aiohttp.ClientSession, lib_id: str, doc_id: str) -> Document:
        """并发获取单个火山云文档"""
        async with semaphore:
            try:
                # 使用 aiohttp 异步请求
                url = "https://www.volcengine.com/api/doc/getDocDetail"
                params = {"LibraryID": lib_id, "DocumentID": doc_id, "type": "online"}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        return None
                    result = await resp.json()
                
                data = result.get("Result", {})
                if not data:
                    return None
                
                title = data.get("Title", "")
                # 优先使用 MDContent
                text = data.get("MDContent", "")
                if not text:
                    # 尝试从 Content JSON 提取
                    content_json = data.get("Content", "")
                    if content_json:
                        text = crawler._extract_text_from_content(content_json)
                
                if not text:
                    return None
                
                doc_url = f"https://www.volcengine.com/docs/{lib_id}/{doc_id}"
                return Document(
                    url=doc_url,
                    title=title or f"doc-{doc_id}",
                    content=text,
                    content_hash=compute_content_hash(text),
                    crawled_at=datetime.now(),
                )
            except Exception as e:
                logging.debug(f"获取火山云文档 {lib_id}/{doc_id} 失败: {e}")
                return None
    
    for product_name in products:
        logging.info(f"[火山云] 正在检查产品: {product_name}")
        try:
            # 同步获取文档列表
            docs_list = crawler.discover_product_docs(product_name, limit=max_docs if max_docs > 0 else 200)
            if not docs_list:
                product_stats.append(f"  - 火山云 {product_name}: 未找到文档")
                continue
            
            docs_to_fetch = docs_list if max_docs == 0 else docs_list[:max_docs]
            logging.info(f"[火山云] {product_name}: 开始并发获取 {len(docs_to_fetch)} 篇文档 (并发数: {concurrency})...")
            
            # 并发获取文档
            async with aiohttp.ClientSession(headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "accept": "application/json",
                "x-use-bff-version": "1",
            }) as session:
                tasks = []
                for doc_info in docs_to_fetch:
                    lib_id = doc_info.get("lib_id", "")
                    doc_id = doc_info.get("doc_id", "")
                    if lib_id and doc_id:
                        tasks.append(fetch_single_doc(session, lib_id, doc_id))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 收集成功的文档
            fetched = 0
            for result in results:
                if isinstance(result, Document):
                    new_docs.append(result)
                    scanned_urls.append(result.url)
                    fetched += 1
            
            product_stats.append(f"  - 火山云 {product_name}: {fetched}/{len(docs_list)} 篇")
            logging.info(f"[火山云] {product_name}: 完成，获取 {fetched} 篇")
            
        except Exception as e:
            logging.error(f"[火山云] 检查产品 {product_name} 失败: {e}")
            product_stats.append(f"  - 火山云 {product_name}: 错误 - {e}")
    
    return new_docs, product_stats, scanned_urls


async def main_job() -> None:
    """主任务：检查四大云厂商文档更新并通知"""
    config = get_config()
    
    # 初始化组件
    db_path = config.get("storage.database", "./data/aliyun_docs.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    storage = DocumentStorage(f"sqlite:///{db_path}")
    storage.init_db()
    
    detector = ChangeDetector()
    summarizer = AISummarizer(config)
    notifier = NotificationManager(config)
    
    request_delay = config.get("crawler.request_delay", 1.0)
    start_time = datetime.now()
    
    all_new_docs = []
    all_product_stats = []
    all_scanned_urls = []
    
    # 读取多云配置
    monitor_clouds = config.get("monitor_clouds", {})
    
    # === 阿里云 ===
    aliyun_config = monitor_clouds.get("aliyun", {})
    if aliyun_config.get("enabled", False):
        aliyun_products = aliyun_config.get("products", [])
        if aliyun_products:
            logging.info(f"[阿里云] 开始检查 {len(aliyun_products)} 个产品...")
            crawler = DocumentCrawler(request_delay=request_delay)
            for product_alias in aliyun_products:
                logging.info(f"[阿里云] 正在检查产品: {product_alias}")
                try:
                    aliases = crawler.discover_product_docs(product_alias)
                    if not aliases:
                        all_product_stats.append(f"  - 阿里云 {product_alias}: 未找到文档")
                        continue
                    aliases = aliases[:50]  # 每个产品最多50篇
                    all_scanned_urls.extend([f"https://help.aliyun.com/zh{a}" for a in aliases])
                    product_docs = crawler.crawl_aliases(aliases)
                    all_new_docs.extend(product_docs)
                    all_product_stats.append(f"  - 阿里云 {product_alias}: {len(product_docs)}/{len(aliases)} 篇")
                except Exception as e:
                    logging.error(f"[阿里云] 检查产品 {product_alias} 失败: {e}")
                    all_product_stats.append(f"  - 阿里云 {product_alias}: 错误 - {e}")
    
    # === 腾讯云 ===
    tencent_config = monitor_clouds.get("tencent", {})
    if tencent_config.get("enabled", False):
        tencent_products = tencent_config.get("products", [])
        tencent_max_docs = tencent_config.get("max_docs", 0)  # 0 表示不限制
        if tencent_products:
            limit_desc = f"每产品最多 {tencent_max_docs} 篇" if tencent_max_docs > 0 else "不限制篇数"
            logging.info(f"[腾讯云] 开始检查 {len(tencent_products)} 个产品 ({limit_desc})...")
            tencent_docs, tencent_stats, tencent_urls = await check_tencent_docs(
                tencent_products, storage, detector, summarizer, request_delay, tencent_max_docs
            )
            all_new_docs.extend(tencent_docs)
            all_product_stats.extend(tencent_stats)
            all_scanned_urls.extend(tencent_urls)
    
    # === 百度云 ===
    baidu_config = monitor_clouds.get("baidu", {})
    if baidu_config.get("enabled", False):
        baidu_products = baidu_config.get("products", [])
        if baidu_products:
            logging.info(f"[百度云] 开始检查 {len(baidu_products)} 个产品...")
            baidu_docs, baidu_stats, baidu_urls = await check_baidu_docs(
                baidu_products, storage, detector, summarizer, request_delay
            )
            all_new_docs.extend(baidu_docs)
            all_product_stats.extend(baidu_stats)
            all_scanned_urls.extend(baidu_urls)
    
    # === 火山云 ===
    volcano_config = monitor_clouds.get("volcano", {})
    if volcano_config.get("enabled", False):
        volcano_products = volcano_config.get("products", [])
        volcano_max_docs = volcano_config.get("max_docs", 0)
        if volcano_products:
            limit_desc = f"每产品最多 {volcano_max_docs} 篇" if volcano_max_docs > 0 else "不限制篇数"
            logging.info(f"[火山云] 开始检查 {len(volcano_products)} 个产品 ({limit_desc})...")
            volcano_docs, volcano_stats, volcano_urls = await check_volcano_docs(
                volcano_products, storage, detector, summarizer, request_delay, volcano_max_docs
            )
            all_new_docs.extend(volcano_docs)
            all_product_stats.extend(volcano_stats)
            all_scanned_urls.extend(volcano_urls)
    
    # 兼容旧配置（仅阿里云）
    if not monitor_clouds:
        products = config.get("monitor_products", ["/vpc"])
        logging.info(f"[兼容模式] 使用旧配置，检查阿里云 {len(products)} 个产品...")
        crawler = DocumentCrawler(request_delay=request_delay)
        for product_alias in products:
            try:
                aliases = crawler.discover_product_docs(product_alias)
                if aliases:
                    aliases = aliases[:50]
                    all_scanned_urls.extend([f"https://help.aliyun.com/zh{a}" for a in aliases])
                    product_docs = crawler.crawl_aliases(aliases)
                    all_new_docs.extend(product_docs)
                    all_product_stats.append(f"  - 阿里云 {product_alias}: {len(product_docs)} 篇")
            except Exception as e:
                all_product_stats.append(f"  - 阿里云 {product_alias}: 错误 - {e}")
    
    if not all_new_docs:
        logging.warning("未获取到任何文档内容")
        return
    
    # 获取历史文档并检测变更
    # 注意：只对本次扫描的URL进行变更检测，避免把未扫描的文档误判为删除
    old_docs_filtered = [doc for doc in storage.get_all_documents() if doc.url in all_scanned_urls]
    change_report = detector.detect_changes(old_docs_filtered, all_new_docs)
    
    # 保存新文档
    for doc in all_new_docs:
        try:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)
        except Exception as e:
            logging.error(f"保存文档失败 {doc.url}: {e}")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    # 统计变更（added/deleted 是 Document，modified 是 DocumentChange）
    added_docs = change_report.added
    modified_changes = change_report.modified
    deleted_docs = change_report.deleted
    total_changes = len(added_docs) + len(modified_changes) + len(deleted_docs)
    
    logging.info(f"检测到 {total_changes} 处变更: 新增 {len(added_docs)}, 修改 {len(modified_changes)}, 删除 {len(deleted_docs)}")
    
    # 生成 AI 摘要（只对修改的文档生成摘要）
    change_summaries = []
    for change in modified_changes[:20]:
        try:
            summary = summarizer.summarize_change(change)
            change.summary = summary
            change_summaries.append(f"- {change.document.title}: {summary}")
        except Exception as e:
            logging.error(f"生成摘要失败: {e}")
    
    # 添加新增文档的标题
    for doc in added_docs[:10]:
        change_summaries.append(f"- [新增] {doc.title}")
    
    # 生成总体摘要（即使无变更也发送通知）
    if total_changes == 0:
        overall_summary = f"定时检查完成，共扫描 {len(all_new_docs)} 篇文档，无变更"
    else:
        overall_summary = f"检测到 {total_changes} 处变更: 新增 {len(added_docs)} 篇, 修改 {len(modified_changes)} 篇, 删除 {len(deleted_docs)} 篇"
    
    # 发送通知
    try:
        notifier.notify_changes(change_report, overall_summary)
        logging.info("通知发送成功")
    except Exception as e:
        logging.error(f"发送通知失败: {e}")
    
    # 输出结果
    result_lines = [
        f"## 多云文档检查完成 (耗时 {elapsed:.1f}s)",
        f"",
        f"### 产品统计",
        *all_product_stats,
        f"",
        f"### 变更统计",
        f"- 新增: {len(added_docs)} 篇",
        f"- 修改: {len(modified_changes)} 篇",
        f"- 删除: {len(deleted_docs)} 篇",
    ]
    logging.info("\n".join(result_lines))


async def run_scheduler() -> None:
    """运行定时任务服务"""
    config = get_config()
    setup_logging(config)
    
    scheduler_config = config.get("scheduler", {})
    cron_expr = scheduler_config.get("cron", "0 10 * * *")
    timezone = scheduler_config.get("timezone", "Asia/Shanghai")
    enabled = scheduler_config.get("enabled", True)
    
    if not enabled:
        logging.warning("定时任务已禁用 (scheduler.enabled=false)")
        return
    
    scheduler = Scheduler(
        cron_expr=cron_expr,
        timezone=timezone,
        job_name="cloud_doc_monitor",
    )
    
    # 设置信号处理
    def signal_handler(sig, frame):
        logging.info(f"收到信号 {sig}，正在停止...")
        scheduler.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logging.info("=" * 60)
    logging.info("云文档监控助手 - 定时任务服务")
    logging.info("=" * 60)
    logging.info(f"cron 表达式: {cron_expr}")
    logging.info(f"时区: {timezone}")
    logging.info(f"监控产品: {config.get('monitor_products', [])}")
    logging.info("=" * 60)
    
    await scheduler.start(main_job)


async def run_once() -> None:
    """立即执行一次检查"""
    config = get_config()
    setup_logging(config)
    
    logging.info("=" * 60)
    logging.info("云文档监控助手 - 立即执行")
    logging.info("=" * 60)
    
    await main_job()


def main() -> None:
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="云文档监控助手 - 自动监控云厂商文档更新",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m src.main              # 启动定时任务服务
  python -m src.main --check-now  # 立即执行一次检查
  python -m src.main --cron "0 9 * * *"  # 使用自定义 cron 表达式
        """,
    )
    
    parser.add_argument(
        "--check-now",
        action="store_true",
        help="立即执行一次检查，不启动定时服务",
    )
    
    parser.add_argument(
        "--cron",
        type=str,
        help="覆盖配置文件中的 cron 表达式",
    )
    
    parser.add_argument(
        "--products",
        type=str,
        help="覆盖监控产品列表，逗号分隔（如 /vpc,/dns）",
    )
    
    args = parser.parse_args()
    
    # 如果指定了命令行参数，覆盖配置
    if args.cron or args.products:
        config = get_config()
        if args.cron:
            config._config["scheduler"]["cron"] = args.cron
        if args.products:
            config._config["monitor_products"] = args.products.split(",")
    
    if args.check_now:
        asyncio.run(run_once())
    else:
        asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()

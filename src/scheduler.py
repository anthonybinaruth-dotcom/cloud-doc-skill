"""任务调度模块"""

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .crawler import DocumentCrawler
from .detector import ChangeDetector
from .notifier import NotificationManager
from .storage import DocumentStorage
from .summarizer import AISummarizer


class DocumentMonitorScheduler:
    """文档监控调度器"""

    def __init__(
        self,
        config: Config,
        storage: DocumentStorage,
        crawler: DocumentCrawler,
        detector: ChangeDetector,
        summarizer: AISummarizer,
    ):
        self.config = config
        self.storage = storage
        self.crawler = crawler
        self.detector = detector
        self.summarizer = summarizer

        self.scheduler = BackgroundScheduler()
        self._is_running = False
        
        # 初始化通知管理器
        self.notifier = NotificationManager(config.get_all())

    def start(self) -> None:
        """启动调度器"""
        if self._is_running:
            logging.warning("调度器已在运行中")
            return

        cron_expr = self.config.get("scheduler.cron", "0 9 * * 1")
        timezone = self.config.get("scheduler.timezone", "Asia/Shanghai")

        # 解析cron表达式
        parts = cron_expr.split()
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=timezone,
        )

        self.scheduler.add_job(
            self.run_check_now,
            trigger=trigger,
            id="doc_monitor_check",
            name="文档监控检查",
            replace_existing=True,
        )

        self.scheduler.start()
        self._is_running = True
        logging.info(f"调度器已启动，Cron: {cron_expr}, 时区: {timezone}")

    def stop(self) -> None:
        """停止调度器"""
        if not self._is_running:
            return

        self.scheduler.shutdown(wait=False)
        self._is_running = False
        logging.info("调度器已停止")

    def run_check_now(self) -> None:
        """立即执行一次文档检查"""
        logging.info("=" * 50)
        logging.info("开始执行文档检查任务")
        logging.info("=" * 50)

        started_at = datetime.now()
        scan_id = self.storage.save_scan_record(started_at=started_at, status="running")

        try:
            # 1. 获取旧文档
            old_docs = self.storage.get_all_documents()
            logging.info(f"已有 {len(old_docs)} 个历史文档")

            # 2. 按产品列表爬取文档
            # 支持列表格式或逗号分隔的字符串格式
            monitor_products = self.config.get("monitor_products", [])
            if isinstance(monitor_products, str):
                # 从环境变量读取时是逗号分隔的字符串
                monitor_products = [p.strip() for p in monitor_products.split(",") if p.strip()]
            new_docs = []

            if monitor_products:
                for product_alias in monitor_products:
                    logging.info(f"正在检查产品: {product_alias}")
                    try:
                        product_docs = self.crawler.crawl_product(
                            product_alias,
                            max_pages=self.config.get("crawler.max_pages_per_product", 100),
                        )
                        new_docs.extend(product_docs)
                        logging.info(f"产品 {product_alias} 获取 {len(product_docs)} 个文档")
                    except Exception as e:
                        logging.error(f"产品 {product_alias} 检查失败: {e}")
            else:
                # fallback: 爬取配置的 base_url
                base_url = self.config.get("crawler.base_url")
                new_docs = self.crawler.crawl_site(base_url)

            logging.info(f"本次共获取 {len(new_docs)} 个文档")

            # 3. 保存新文档，并建立 URL -> ID 映射
            url_to_id = {}
            for doc in new_docs:
                doc_id = self.storage.save_document(doc)
                url_to_id[doc.url] = doc_id
                self.storage.save_version(doc_id, doc.content, doc.content_hash)

            # 4. 检测变更
            report = self.detector.detect_changes(old_docs, new_docs)

            # 5. 生成摘要
            summary = ""
            if report.modified:
                summary = self.summarizer.summarize_batch(report.modified)

                # 保存变更记录
                for change in report.modified:
                    doc_url = change.document.url
                    doc_id = url_to_id.get(doc_url)
                    if doc_id:
                        individual_summary = self.summarizer.summarize_change(change)
                        self.storage.save_change(
                            scan_id=scan_id,
                            document_id=doc_id,
                            change_type=change.change_type.value,
                            diff=change.diff,
                            summary=individual_summary,
                        )

            # 6. 更新扫描记录
            total_changes = len(report.added) + len(report.modified) + len(report.deleted)
            self.storage.update_scan_record(
                scan_id=scan_id,
                completed_at=datetime.now(),
                status="completed",
                documents_scanned=len(new_docs),
                changes_detected=total_changes,
            )

            # 7. 发送通知
            if total_changes > 0:
                notify_results = self.notifier.notify_changes(report, summary)
                logging.info(f"通知发送结果: {notify_results}")

            logging.info(f"文档检查任务完成，检测到 {total_changes} 个变更")

        except Exception as e:
            logging.error(f"文档检查任务失败: {e}")
            self.storage.update_scan_record(
                scan_id=scan_id,
                completed_at=datetime.now(),
                status="failed",
                error_message=str(e),
            )

    def get_next_run_time(self) -> Optional[datetime]:
        """获取下次执行时间"""
        job = self.scheduler.get_job("doc_monitor_check")
        if job:
            return job.next_run_time
        return None

    @property
    def is_running(self) -> bool:
        return self._is_running

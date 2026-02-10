"""主程序入口"""

import argparse
import logging
import signal
import sys
from pathlib import Path

from .config import get_config
from .crawler import DocumentCrawler
from .detector import ChangeDetector
from .scheduler import DocumentMonitorScheduler
from .storage import DocumentStorage
from .summarizer import AISummarizer, HuggingFaceAdapter, OllamaAdapter
from .utils import setup_logging


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="阿里云文档监控助手")
    parser.add_argument(
        "--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "--check-now", action="store_true", help="立即执行一次检查（不启动定时任务）"
    )
    parser.add_argument(
        "--init-db", action="store_true", help="初始化数据库"
    )
    return parser.parse_args()


def create_llm_adapter(config):
    """根据配置创建LLM适配器"""
    provider = config.get("llm.provider", "huggingface")

    if provider == "huggingface":
        return HuggingFaceAdapter(
            model=config.get("llm.model", "Qwen/Qwen2.5-7B-Instruct"),
            api_key=config.get("llm.api_key", ""),
            api_base=config.get("llm.api_base", "https://api-inference.huggingface.co/models"),
        )
    elif provider == "ollama":
        return OllamaAdapter(
            model=config.get("llm.model", "qwen2.5:7b"),
            api_base=config.get("llm.api_base", "http://localhost:11434"),
        )
    else:
        raise ValueError(f"不支持的LLM提供商: {provider}")


def main():
    """主函数"""
    args = parse_args()

    # 加载配置
    config = get_config(args.config)

    # 设置日志
    setup_logging(
        level=config.get("logging.level", "INFO"),
        log_file=config.get("logging.file"),
    )

    logging.info("阿里云文档监控助手启动中...")

    # 初始化存储
    db_path = config.get("storage.database", "./data/aliyun_docs.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    storage = DocumentStorage(f"sqlite:///{db_path}")

    if args.init_db:
        storage.init_db()
        logging.info("数据库初始化完成")
        return

    # 确保数据库已初始化
    storage.init_db()

    # 创建各模块
    crawler = DocumentCrawler(
        base_url=config.get("crawler.base_url", "https://help.aliyun.com"),
        request_delay=config.get("crawler.request_delay", 1.0),
        max_retries=config.get("crawler.max_retries", 3),
        timeout=config.get("crawler.timeout", 30),
        user_agent=config.get("crawler.user_agent", "AliyunDocMonitor/1.0"),
    )

    detector = ChangeDetector()

    llm_adapter = create_llm_adapter(config)
    summarizer = AISummarizer(llm_adapter, max_tokens=config.get("llm.max_tokens", 1000))

    # 创建调度器
    scheduler = DocumentMonitorScheduler(
        config=config,
        storage=storage,
        crawler=crawler,
        detector=detector,
        summarizer=summarizer,
    )

    if args.check_now:
        # 立即执行一次检查
        logging.info("执行手动检查...")
        scheduler.run_check_now()
        return

    # 启动定时任务
    scheduler.start()
    logging.info(f"下次执行时间: {scheduler.get_next_run_time()}")

    # 优雅关闭处理
    def signal_handler(signum, frame):
        logging.info("收到关闭信号，正在停止...")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 保持运行
    logging.info("服务已启动，按 Ctrl+C 停止")
    try:
        signal.pause()
    except AttributeError:
        # Windows不支持signal.pause()
        import time
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()

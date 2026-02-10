"""初始化扫描 - 全量抓取指定产品文档存入数据库"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_config
from src.crawler import DocumentCrawler
from src.storage import DocumentStorage
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

config = get_config()
db_path = config.get("storage.database", "./data/aliyun_docs.db")
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
storage = DocumentStorage(f"sqlite:///{db_path}")
storage.init_db()

crawler = DocumentCrawler(request_delay=0.3)

products = config.get("monitor_products", ["/vpc"])

for product in products:
    logging.info(f"开始扫描产品: {product}")
    aliases = crawler.discover_product_docs(product)
    logging.info(f"发现 {len(aliases)} 个文档")

    for i, alias in enumerate(aliases, 1):
        # 跳过 OpenAPI 参考文档（JSON API 不支持）
        if "/api-" in alias and "-20" in alias:
            logging.info(f"  [{i}/{len(aliases)}] 跳过API文档: {alias}")
            continue
        try:
            data = crawler.fetch_doc_by_alias(alias)
            if data is None:
                logging.warning(f"  [{i}/{len(aliases)}] 跳过: {alias}")
                continue
            doc = crawler.parse_api_response(data, alias)
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)
            logging.info(f"  [{i}/{len(aliases)}] 已存储: {doc.title}")
        except Exception as e:
            logging.error(f"  [{i}/{len(aliases)}] 失败 {alias}: {e}")

logging.info("扫描完成!")

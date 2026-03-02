"""百度云文档爬虫模块"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


# 百度云文档 page-data API 模板
BAIDU_PAGE_DATA_URL = "https://bce.bdstatic.com/p3m/bce-doc/online/{product}/doc/{product}/s/page-data/{slug}/page-data.json"
# 百度云文档页面 URL 模板
BAIDU_DOC_URL = "https://cloud.baidu.com/doc/{product}/s/{slug}"


class BaiduDocCrawler:
    """百度云文档爬虫 - 基于 Gatsby page-data API"""

    def __init__(self, request_delay: float = 0.5, timeout: int = 30):
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "CloudDocMonitor/1.0"})
        self.last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    def discover_product_docs(self, product: str, seed_slug: str = None) -> List[Dict[str, str]]:
        """从百度云文档页面的侧边栏提取所有文档链接。

        通过抓取一个实际文档页面的 HTML，解析侧边栏中的链接获取完整目录。
        index.html 侧边栏不完整，必须用实际文档页面。

        Args:
            product: 产品名，如 VPC、BCC、BOS
            seed_slug: 用于获取侧边栏的种子文档 slug（可选，默认自动发现）

        Returns:
            文档列表，每项包含 slug, title, url
        """
        self._rate_limit()
        product_upper = product.upper()

        if seed_slug:
            seed_url = BAIDU_DOC_URL.format(product=product_upper, slug=seed_slug)
        else:
            # 先从 index.html 找一个种子文档
            index_url = f"https://cloud.baidu.com/doc/{product_upper}/index.html"
            try:
                resp = self.session.get(index_url, timeout=self.timeout)
                resp.raise_for_status()
                pattern = rf'href="/doc/{product_upper}/s/([^"]+)"'
                match = re.search(pattern, resp.text, re.IGNORECASE)
                if match:
                    seed_url = BAIDU_DOC_URL.format(product=product_upper, slug=match.group(1))
                else:
                    logging.error(f"百度云 {product_upper} 首页未找到文档链接")
                    return []
            except Exception as e:
                logging.error(f"获取百度云 {product_upper} 首页失败: {e}")
                return []

        # 用实际文档页面获取完整侧边栏
        try:
            self._rate_limit()
            resp = self.session.get(seed_url, timeout=self.timeout)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logging.error(f"获取百度云文档页面失败: {e}")
            return []

        pattern = rf'href="(/doc/{product_upper}/s/([^"]+))"[^>]*>([^<]*)<'
        matches = re.findall(pattern, html, re.IGNORECASE)

        docs = []
        seen_slugs = set()
        for href, slug, title in matches:
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                docs.append({
                    "slug": slug,
                    "title": title.strip(),
                    "url": f"https://cloud.baidu.com{href}",
                })

        logging.info(f"发现百度云 {product_upper} 文档 {len(docs)} 篇")
        return docs

    def fetch_doc(self, product: str, slug: str) -> Optional[Dict]:
        """通过 page-data API 获取单篇百度云文档。

        Args:
            product: 产品名，如 VPC
            slug: 文档 slug，如 qjwvyu0at

        Returns:
            包含 title, date, text, html, url 的字典
        """
        self._rate_limit()
        product_upper = product.upper()
        api_url = BAIDU_PAGE_DATA_URL.format(product=product_upper, slug=slug)

        try:
            resp = self.session.get(api_url, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error(f"获取百度云文档失败 {slug}: {e}")
            return None

        try:
            mr = data["result"]["data"]["markdownRemark"]
            html_content = mr.get("html", "")
            fields = mr.get("fields", {})

            soup = BeautifulSoup(html_content, "lxml")
            text = soup.get_text(separator="\n", strip=True)

            return {
                "title": fields.get("title", slug),
                "date": fields.get("date", ""),
                "text": text,
                "html": html_content,
                "url": BAIDU_DOC_URL.format(product=product_upper, slug=slug),
                "slug": slug,
            }
        except (KeyError, TypeError) as e:
            logging.error(f"解析百度云文档失败 {slug}: {e}")
            return None

    def fetch_docs_batch(
        self, product: str, slugs: List[str], max_pages: int = None
    ) -> List[Dict]:
        """批量获取百度云文档。"""
        docs = []
        for slug in slugs:
            if max_pages and len(docs) >= max_pages:
                break
            doc = self.fetch_doc(product, slug)
            if doc:
                docs.append(doc)
                logging.info(f"已获取百度云文档 {len(docs)}/{len(slugs)}: {doc['title']}")
        return docs

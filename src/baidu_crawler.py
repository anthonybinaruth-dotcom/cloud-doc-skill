"""百度云文档爬虫模块"""

import logging
import re
import time
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BAIDU_SEARCH_API = "https://cloud.baidu.com/api/portalsearch"
BAIDU_PAGE_DATA_URL = "https://bce.bdstatic.com/p3m/bce-doc/online/{product}/doc/{product}/s/page-data/{slug}/page-data.json"
BAIDU_DOC_URL = "https://cloud.baidu.com/doc/{product}/s/{slug}"


class BaiduDocCrawler:
    """百度云文档爬虫 - 基于搜索 API + Gatsby page-data API"""

    def __init__(self, request_delay: float = 0.5, timeout: int = 30):
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://cloud.baidu.com",
            "Referer": "https://cloud.baidu.com/search",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })
        self.last_request_time = 0.0
        self.search_uid = uuid.uuid4().hex

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    @staticmethod
    def _strip_html_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", (text or "")).strip()

    @staticmethod
    def _parse_doc_url(url: str) -> Tuple[str, str]:
        match = re.search(r"/doc/([A-Za-z0-9_-]+)/s/([^/?#]+)", (url or "").strip())
        if not match:
            return "", ""
        return match.group(1).upper(), match.group(2)

    @staticmethod
    def _extract_image_urls_from_soup(soup: BeautifulSoup, base_url: str) -> List[str]:
        seen: set = set()
        image_urls: List[str] = []
        for img in soup.find_all("img"):
            candidate = ""
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                value = str(img.get(attr, "") or "").strip()
                if value:
                    candidate = value
                    break
            if not candidate:
                continue
            normalized = urljoin(base_url, candidate)
            if normalized not in seen:
                seen.add(normalized)
                image_urls.append(normalized)
        return image_urls

    def search_docs(self, query: str, product: str = "", limit: int = 20) -> List[Dict[str, str]]:
        query_text = (query or "").strip()
        product_upper = (product or "").strip().upper()
        if not query_text:
            return []

        page_no = 1
        page_size = 10
        max_pages = 100
        docs: List[Dict[str, str]] = []
        seen: set = set()

        while page_no <= max_pages:
            self._rate_limit()
            payload = {
                "query": query_text,
                "type": "doc",
                "pageNo": str(page_no),
                "uid": self.search_uid,
                "status": "ONLINE",
            }
            try:
                resp = self.session.post(
                    BAIDU_SEARCH_API,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Referer": f"https://cloud.baidu.com/search/{query_text}/all-{page_no}"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logging.error(f"搜索百度云文档失败(query={query_text}, page={page_no}): {e}")
                break

            result = data.get("result", {}) if isinstance(data, dict) else {}
            data_list = result.get("dataList", []) if isinstance(result, dict) else []
            if not isinstance(data_list, list) or not data_list:
                break

            for item in data_list:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "") or "").strip()
                item_product, slug = self._parse_doc_url(url)
                if not item_product or not slug:
                    continue
                if product_upper and item_product != product_upper:
                    continue
                key = (item_product, slug)
                if key in seen:
                    continue
                seen.add(key)
                docs.append({
                    "product": item_product,
                    "slug": slug,
                    "title": self._strip_html_tags(str(item.get("title", "") or slug)),
                    "url": url,
                    "keywords": self._strip_html_tags(str(item.get("keywords", "") or "")),
                    "snippet": self._strip_html_tags(str(item.get("content", "") or "")),
                })
                if limit > 0 and len(docs) >= limit:
                    logging.info(f"发现百度云 {product_upper or query_text} 文档 {len(docs)} 篇")
                    return docs

            search_info = result.get("searchInfo", {}) if isinstance(result, dict) else {}
            total_num = 0
            if isinstance(search_info, dict):
                try:
                    total_num = int(search_info.get("totalNum", 0) or 0)
                except Exception:
                    total_num = 0
            if len(data_list) < page_size:
                break
            if total_num and page_no * page_size >= total_num:
                break
            page_no += 1

        logging.info(f"发现百度云 {product_upper or query_text} 文档 {len(docs)} 篇")
        return docs

    def discover_product_docs(self, product: str, limit: int = 0) -> List[Dict[str, str]]:
        product_upper = product.upper()
        return self.search_docs(query=product_upper, product=product_upper, limit=limit or 0)

    def fetch_doc(self, product: str, slug: str) -> Optional[Dict]:
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
            doc_url = BAIDU_DOC_URL.format(product=product_upper, slug=slug)
            image_urls = self._extract_image_urls_from_soup(soup, doc_url)
            return {
                "title": fields.get("title", slug),
                "date": fields.get("date", ""),
                "last_modified": fields.get("date", ""),
                "text": text,
                "html": html_content,
                "url": doc_url,
                "slug": slug,
                "image_urls": image_urls,
            }
        except (KeyError, TypeError) as e:
            logging.error(f"解析百度云文档失败 {slug}: {e}")
            return None

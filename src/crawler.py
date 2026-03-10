"""文档爬虫模块 - 使用阿里云文档 JSON API"""

import logging
import random
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import Document
from .utils import compute_content_hash

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]

_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://help.aliyun.com/",
}

ALIYUN_DOC_API = "https://help.aliyun.com/help/json/document_detail.json"
ALIYUN_MENU_API = "https://help.aliyun.com/help/json/menupath.json"


def url_to_alias(doc_url: str) -> str:
    path = urlparse(doc_url).path
    if path.startswith("/zh/"):
        path = path[3:]
    elif path.startswith("/zh"):
        path = path[3:]
    return path.rstrip("/").split("?")[0]


def alias_to_url(alias: str) -> str:
    if not alias.startswith("/"):
        alias = "/" + alias
    return f"https://help.aliyun.com/zh{alias}"


class DocumentCrawler:
    """文档爬虫类 - 基于阿里云 JSON API"""

    def __init__(self, request_delay: float = 1.0, max_retries: int = 3, timeout: int = 30):
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({**_BROWSER_HEADERS, "User-Agent": random.choice(_USER_AGENTS)})
        self.last_request_time = 0.0
        self._consecutive_failures = 0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        jitter = random.uniform(0.2, 0.8)
        delay = self.request_delay + jitter
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request_time = time.time()

    @staticmethod
    def _normalize_alias(alias: str) -> str:
        alias = alias.strip().lower()
        if not alias.startswith("/"):
            alias = "/" + alias
        return alias.rstrip("/")

    def _rotate_user_agent(self) -> None:
        self.session.headers["User-Agent"] = random.choice(_USER_AGENTS)

    def _fetch_api_data(self, api_url: str, params: dict, api_name: str) -> Optional[dict]:
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                resp = self.session.get(api_url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    raise RuntimeError(f"{api_name} 返回非 JSON 响应")
                result = resp.json()
                if result.get("code") == 200 and result.get("data") is not None:
                    self._consecutive_failures = 0
                    return result["data"]
                code = result.get("code")
                raise RuntimeError(f"{api_name} 返回异常 code={code}")
            except Exception as exc:
                last_error = exc
                logging.error(f"{api_name} 失败 (尝试 {attempt}/{self.max_retries}): {exc}")
                if attempt < self.max_retries:
                    wait = min(3 * (2 ** (attempt - 1)), 30)
                    self._rotate_user_agent()
                    time.sleep(wait)
        self._consecutive_failures += 1
        logging.error(f"{api_name} 最终失败: params={params}, error={last_error}")
        return None

    def fetch_doc_by_alias(self, alias: str) -> Optional[dict]:
        alias = self._normalize_alias(alias)
        params = {"alias": alias, "pageNum": 1, "pageSize": 20, "website": "cn", "language": "zh", "channel": ""}
        return self._fetch_api_data(ALIYUN_DOC_API, params, api_name="文档详情 API")

    def fetch_menu(self, alias: str) -> Optional[dict]:
        alias = self._normalize_alias(alias)
        params = {"alias": alias, "website": "cn", "language": "zh", "channel": ""}
        return self._fetch_api_data(ALIYUN_MENU_API, params, api_name="侧边栏 API")

    def extract_aliases_from_menu(self, menu_data: dict) -> List[str]:
        aliases = []
        def _walk(node: dict):
            if node.get("validDocument") and node.get("alias"):
                aliases.append(node["alias"])
            for child in node.get("children", []):
                _walk(child)
        _walk(menu_data)
        return aliases

    @staticmethod
    def extract_image_urls_from_html(html_content: str) -> List[str]:
        soup = BeautifulSoup(html_content, "lxml")
        image_urls = []
        seen = set()
        for image in soup.find_all("img"):
            src = str(image.get("src", "") or "").strip()
            if not src or src in seen:
                continue
            seen.add(src)
            image_urls.append(src)
        return image_urls

    @staticmethod
    def extract_hetu_diagrams_from_html(html_content: str) -> List[dict]:
        soup = BeautifulSoup(html_content, "lxml")
        diagrams = []
        for node in soup.find_all("hetu"):
            image = node.find("img")
            diagrams.append({
                "type": str(node.get("type", "") or "").strip(),
                "hetu_id": str(node.get("hetuid", "") or "").strip(),
                "node_id": str(node.get("id", "") or "").strip(),
                "version_id": str(node.get("versionid", "") or "").strip(),
                "uuid": str(node.get("uuid", "") or "").strip(),
                "image_url": str(image.get("src", "") or "").strip() if image else "",
                "alt": str(image.get("alt", "") or "").strip() if image else "",
            })
        return diagrams

    def discover_product_docs(self, product_alias: str, strict: bool = False) -> List[str]:
        menu_data = self.fetch_menu(product_alias)
        if menu_data is None:
            message = f"获取阿里云产品目录失败: {self._normalize_alias(product_alias)}"
            if strict:
                raise RuntimeError(message)
            logging.error(message)
            return []
        aliases = self.extract_aliases_from_menu(menu_data)
        logging.info(f"发现 {len(aliases)} 个文档 (产品: {menu_data.get('alias', product_alias)})")
        return aliases

    def parse_api_response(self, data: dict, alias: str) -> Document:
        url = alias_to_url(alias)
        html_content = data.get("content", "")
        soup = BeautifulSoup(html_content, "lxml")
        image_urls = self.extract_image_urls_from_html(html_content)
        hetu_diagrams = self.extract_hetu_diagrams_from_html(html_content)
        text_content = "\n".join(
            line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()
        )
        title = data.get("title", "")
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else alias.split("/")[-1]
        last_modified = None
        last_modified_ms = data.get("lastModifiedTime")
        if last_modified_ms:
            last_modified = datetime.fromtimestamp(last_modified_ms / 1000)
        return Document(
            url=url,
            title=title,
            content=text_content,
            content_hash=compute_content_hash(text_content),
            last_modified=last_modified,
            crawled_at=datetime.now(),
            metadata={
                "desc": str(data.get("desc", "") or "").strip(),
                "node_id": data.get("nodeId"),
                "version": data.get("version"),
                "product_url": str(data.get("productUrl", "") or "").strip(),
                "developer_url": str(data.get("developerUrl", "") or "").strip(),
                "path": str(data.get("path", "") or "").strip(),
                "seo_title": str(data.get("seoTitle", "") or "").strip(),
                "image_urls": image_urls,
                "image_count": len(image_urls),
                "hetu_diagrams": hetu_diagrams,
                "hetu_count": len(hetu_diagrams),
            },
        )

    def crawl_page(self, url: str) -> Document:
        if url.startswith("http"):
            alias = url_to_alias(url)
        else:
            alias = url if url.startswith("/") else f"/{url}"
        logging.info(f"正在获取文档: {alias}")
        data = self.fetch_doc_by_alias(alias)
        if data is None:
            raise RuntimeError(f"无法获取文档: {alias}")
        return self.parse_api_response(data, alias)

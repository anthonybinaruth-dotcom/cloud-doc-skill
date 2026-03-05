"""文档爬虫模块 - 使用阿里云文档 JSON API"""

import logging
import random
import time
from datetime import datetime
from typing import List, Optional, Set
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .models import Document
from .utils import compute_content_hash

# 常见浏览器 User-Agent 池
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
]

# 模拟浏览器的通用请求头（来自 Chrome 真实请求）
_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Referer": "https://help.aliyun.com/",
    "bx-v": "2.5.36",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# 阿里云文档 JSON API
ALIYUN_DOC_API = "https://help.aliyun.com/help/json/document_detail.json"
ALIYUN_MENU_API = "https://help.aliyun.com/help/json/menupath.json"


def url_to_alias(doc_url: str) -> str:
    """从文档页面 URL 提取 alias。
    https://help.aliyun.com/zh/ecs/user-guide/what-is-ecs -> /ecs/user-guide/what-is-ecs
    """
    path = urlparse(doc_url).path
    if path.startswith("/zh/"):
        path = path[3:]
    elif path.startswith("/zh"):
        path = path[3:]
    return path.rstrip("/").split("?")[0]


def alias_to_url(alias: str) -> str:
    """从 alias 生成文档页面 URL"""
    if not alias.startswith("/"):
        alias = "/" + alias
    return f"https://help.aliyun.com/zh{alias}"


class DocumentCrawler:
    """文档爬虫类 - 基于阿里云 JSON API"""

    def __init__(
        self,
        base_url: str = "https://help.aliyun.com",
        request_delay: float = 1.0,
        max_retries: int = 3,
        timeout: int = 30,
        user_agent: str = "",
    ):
        self.base_url = base_url
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.user_agent = user_agent or random.choice(_USER_AGENTS)
        self.visited_urls: Set[str] = set()
        self.session = requests.Session()
        self.session.headers.update({**_BROWSER_HEADERS, "User-Agent": self.user_agent})
        self.last_request_time = 0.0
        self._consecutive_failures = 0  # 连续失败计数

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        # 在基础间隔上增加随机抖动，避免固定节奏
        jitter = random.uniform(0.2, 0.8)
        delay = self.request_delay + jitter
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request_time = time.time()

    # ========== JSON API 方法 ==========

    @staticmethod
    def _normalize_alias(alias: str) -> str:
        """规范化 alias：小写、确保以 / 开头、去除尾部斜杠"""
        alias = alias.strip().lower()
        if not alias.startswith("/"):
            alias = "/" + alias
        return alias.rstrip("/")

    def fetch_doc_by_alias(self, alias: str) -> Optional[dict]:
        """通过 alias 调用文档详情 API"""
        alias = self._normalize_alias(alias)
        params = {
            "alias": alias,
            "pageNum": 1,
            "pageSize": 20,
            "website": "cn",
            "language": "zh",
            "channel": "",
        }
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            self._rate_limit()
            try:
                resp = self.session.get(ALIYUN_DOC_API, params=params, timeout=self.timeout)
                resp.raise_for_status()
                # 检查 Content-Type，防止反爬页面返回 HTML 被当作 JSON 解析
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    logging.warning(
                        f"API 返回非 JSON 响应 (Content-Type: {content_type}), "
                        f"可能触发了反爬机制, alias={alias}"
                    )
                    if attempt < self.max_retries:
                        # 指数退避 + 换 UA
                        wait = min(10 * (2 ** (attempt - 1)), 120)
                        self.session.headers["User-Agent"] = random.choice(_USER_AGENTS)
                        logging.info(f"触发风控，等待 {wait}s 后重试，已更换 UA")
                        time.sleep(wait)
                        continue
                    return None
                result = resp.json()
                if result.get("code") == 200 and result.get("data"):
                    self._consecutive_failures = 0
                    return result["data"]
                logging.warning(f"API 返回异常: code={result.get('code')}, alias={alias}")
                return None
            except Exception as e:
                last_error = e
                logging.error(f"文档 API 失败 (尝试 {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    time.sleep(3 * (2 ** (attempt - 1)))
        self._consecutive_failures += 1
        logging.error(f"文档 API 最终失败: alias={alias}, error={last_error}")
        return None

    def fetch_menu(self, alias: str) -> Optional[dict]:
        """通过侧边栏 API 获取产品文档目录树"""
        alias = self._normalize_alias(alias)
        self._rate_limit()
        params = {
            "alias": alias,
            "website": "cn",
            "language": "zh",
            "channel": "",
        }
        try:
            resp = self.session.get(ALIYUN_MENU_API, params=params, timeout=self.timeout)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                logging.warning(f"侧边栏 API 返回非 JSON 响应, 可能触发了反爬机制")
                return None
            result = resp.json()
            if result.get("code") == 200 and result.get("data"):
                return result["data"]
            logging.warning(f"侧边栏 API 返回异常: {result.get('code')}")
            return None
        except Exception as e:
            logging.error(f"侧边栏 API 失败: {e}")
            return None

    def extract_aliases_from_menu(self, menu_data: dict) -> List[str]:
        """从目录树中递归提取所有文档的 alias"""
        aliases = []
        def _walk(node: dict):
            if node.get("validDocument") and node.get("alias"):
                aliases.append(node["alias"])
            for child in node.get("children", []):
                _walk(child)
        _walk(menu_data)
        return aliases

    def discover_product_docs(self, product_alias: str) -> List[str]:
        """发现某个产品下的所有文档 alias"""
        menu_data = self.fetch_menu(product_alias)
        if menu_data is None:
            return []
        aliases = self.extract_aliases_from_menu(menu_data)
        logging.info(f"发现 {len(aliases)} 个文档 (产品: {menu_data.get('alias', product_alias)})")
        return aliases

    # ========== 文档解析与爬取 ==========

    def parse_api_response(self, data: dict, alias: str) -> Document:
        """将 API 返回数据解析为 Document"""
        url = alias_to_url(alias)
        html_content = data.get("content", "")
        soup = BeautifulSoup(html_content, "lxml")
        text_content = soup.get_text(separator="\n", strip=True)
        title = data.get("title", "")
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else alias.split("/")[-1]

        # 提取阿里云文档的实际更新时间
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
        )

    def crawl_page(self, url: str) -> Document:
        """爬取单个文档（支持 URL 或 alias）"""
        if url.startswith("http"):
            alias = url_to_alias(url)
        else:
            alias = url if url.startswith("/") else f"/{url}"
        page_url = alias_to_url(alias)
        if page_url in self.visited_urls:
            raise ValueError(f"URL 已访问: {page_url}")
        logging.info(f"正在获取文档: {alias}")
        data = self.fetch_doc_by_alias(alias)
        if data is None:
            raise RuntimeError(f"无法获取文档: {alias}")
        doc = self.parse_api_response(data, alias)
        self.visited_urls.add(page_url)
        return doc

    def crawl_aliases(self, aliases: List[str], max_pages: int = None) -> List[Document]:
        """批量获取多个文档"""
        documents = []
        for alias in aliases:
            if max_pages and len(documents) >= max_pages:
                break
            # 连续失败超过 5 次，判定被风控，暂停后继续
            if self._consecutive_failures >= 5:
                logging.warning("连续失败 5 次，疑似触发风控，暂停 60s 后继续")
                time.sleep(60)
                self.session.headers["User-Agent"] = random.choice(_USER_AGENTS)
                self._consecutive_failures = 0
            try:
                doc = self.crawl_page(alias)
                documents.append(doc)
                logging.info(f"已获取 {len(documents)}/{len(aliases)}: {doc.title}")
            except Exception as e:
                logging.error(f"获取失败 {alias}: {e}")
        logging.info(f"批量获取完成，共 {len(documents)} 个文档")
        return documents

    def crawl_product(self, product_alias: str, max_pages: int = None) -> List[Document]:
        """爬取整个产品的所有文档"""
        aliases = self.discover_product_docs(product_alias)
        if not aliases:
            return []
        return self.crawl_aliases(aliases, max_pages=max_pages)

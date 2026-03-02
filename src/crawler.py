"""文档爬虫模块 - 使用阿里云文档 JSON API"""

import logging
import time
from datetime import datetime
from typing import List, Optional, Set
from urllib.parse import quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .models import Document
from .utils import compute_content_hash, normalize_url, retry, deduplicate_urls

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
        user_agent: str = "CloudDocMonitor/1.0",
    ):
        self.base_url = base_url
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.visited_urls: Set[str] = set()
        self.robot_parser = RobotFileParser()
        self._init_robots_parser()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        self.last_request_time = 0.0

    def _init_robots_parser(self) -> None:
        try:
            robots_url = urljoin(self.base_url, "/robots.txt")
            self.robot_parser.set_url(robots_url)
            self.robot_parser.read()
        except Exception as e:
            logging.warning(f"无法加载 robots.txt: {e}")

    def _can_fetch(self, url: str) -> bool:
        try:
            return self.robot_parser.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
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
        self._rate_limit()
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
            try:
                resp = self.session.get(ALIYUN_DOC_API, params=params, timeout=self.timeout)
                resp.raise_for_status()
                result = resp.json()
                if result.get("code") == 200 and result.get("data"):
                    return result["data"]
                logging.warning(f"API 返回异常: code={result.get('code')}, alias={alias}")
                return None
            except Exception as e:
                last_error = e
                logging.error(f"文档 API 失败 (尝试 {attempt}/{self.max_retries}): {e}")
                if attempt < self.max_retries:
                    time.sleep(2 * attempt)
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

    # ========== 兼容旧接口 ==========

    @retry(max_attempts=3, delay=1.0, backoff=2.0)
    def _fetch_page(self, url: str) -> str:
        self._rate_limit()
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def parse_document(self, html: str, url: str) -> Document:
        soup = BeautifulSoup(html, "lxml")
        title = ""
        tag = soup.find("title")
        if tag:
            title = tag.get_text().strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text().strip()
        content = ""
        for sel in ["article", ".content", "main", ".markdown-body", "body"]:
            el = soup.select_one(sel)
            if el:
                content = el.get_text(separator="\n", strip=True)
                break
        return Document(
            url=url, title=title, content=content,
            content_hash=compute_content_hash(content),
            crawled_at=datetime.now(),
        )

    def extract_links(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            abs_url = urljoin(base_url, a["href"])
            norm = normalize_url(abs_url, base_url)
            if urlparse(norm).netloc == urlparse(base_url).netloc:
                links.append(norm)
        return deduplicate_urls(links)

    def crawl_site(self, base_url: str, max_pages: int = None) -> List[Document]:
        documents = []
        urls_to_visit = [normalize_url(base_url, self.base_url)]
        while urls_to_visit and (max_pages is None or len(documents) < max_pages):
            url = urls_to_visit.pop(0)
            if url in self.visited_urls:
                continue
            try:
                doc = self.crawl_page(url)
                documents.append(doc)
            except Exception as e:
                logging.error(f"爬取失败 {url}: {e}")
        return documents

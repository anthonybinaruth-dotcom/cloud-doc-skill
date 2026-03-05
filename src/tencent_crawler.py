"""腾讯云文档爬虫模块"""

import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


TENCENT_DOC_DETAIL_API = "https://cloud.tencent.com/document/cgi/document/getDocPageDetail"
TENCENT_NAV_TREE_URL = "https://qcloudimg.tencent-cloud.cn/scripts/qccomponents/v2/full-nav-tree.json"
TENCENT_DOC_URL_TEMPLATE = "https://cloud.tencent.com/document/product/{product_id}/{doc_id}"
TENCENT_SEARCH_API = "https://cloud.tencent.com/search/ajax/searchdoc"
TENCENT_SEARCH_API_V2 = "https://cloud.tencent.com/portal/search/api/result/startup"
_TENCENT_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


class TencentDocCrawler:
    """腾讯云文档爬虫 - 基于目录树 JSON + 文档详情 API"""

    def __init__(self, request_delay: float = 0.5, timeout: int = 30):
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                **_TENCENT_BROWSER_HEADERS,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
                ),
            }
        )
        self.last_request_time = 0.0

    def _rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    @staticmethod
    def _normalize_digits(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, int):
            return str(value)
        text = str(value).strip()
        if not text:
            return ""
        if text.isdigit():
            return text
        match = re.search(r"(\d+)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _pick_first_str(node: Dict[str, Any], keys: List[str]) -> str:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        clean = url.strip()
        if clean.startswith("//"):
            return f"https:{clean}"
        if clean.startswith("/"):
            return f"https://cloud.tencent.com{clean}"
        return clean

    def _fetch_nav_tree(self) -> Optional[Any]:
        self._rate_limit()
        try:
            response = self.session.get(
                TENCENT_NAV_TREE_URL,
                timeout=self.timeout,
                headers={
                    "Referer": "https://cloud.tencent.com/",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logging.error(f"获取腾讯云文档目录失败: {e}")
            return None

    def _search_product_id_by_keyword(self, keyword: str) -> tuple:
        """通过搜索 API 查找产品的 product_id 和中文名称。
        
        从搜索结果的 URL 中提取 product_id，从 productName 获取中文名称。
        
        Returns:
            (product_id, product_name) 元组，未找到时返回 ("", "")
        """
        self._rate_limit()
        try:
            response = self.session.get(
                TENCENT_SEARCH_API,
                params={"keyword": keyword, "page": 1, "pagesize": 10},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            logging.error(f"搜索腾讯云文档失败: {e}")
            return ("", "")
        
        data = result.get("data", {})
        docs = data.get("dataList", [])
        
        # 从搜索结果的 URL 中提取 product_id，同时记录产品名称
        product_info: Dict[str, Dict[str, any]] = {}
        for doc in docs:
            url = doc.get("url", "")
            match = re.search(r"/document/product/(\d+)/", url)
            if match:
                pid = match.group(1)
                product_name = doc.get("productName", "")
                if pid not in product_info:
                    product_info[pid] = {"count": 0, "name": product_name}
                product_info[pid]["count"] += 1
                # 优先保留非空的产品名称
                if product_name and not product_info[pid]["name"]:
                    product_info[pid]["name"] = product_name
        
        if product_info:
            # 返回出现次数最多的 product_id 及其名称
            best_pid = max(product_info.keys(), key=lambda k: product_info[k]["count"])
            best_name = product_info[best_pid]["name"]
            logging.info(f"通过搜索找到产品 '{keyword}' -> product_id: {best_pid}, name: {best_name}")
            return (best_pid, best_name)
        
        return ("", "")

    def _find_product_id_by_name(self, name: str) -> tuple:
        """根据产品名称查找 product_id 和中文名称。
        
        优先使用搜索 API，因为更准确。
        
        Returns:
            (product_id, product_name) 元组
        """
        name_lower = name.strip().lower()
        if not name_lower:
            return ("", "")
        
        # 通过搜索 API 查找（最准确）
        found_id, found_name = self._search_product_id_by_keyword(name)
        if found_id:
            return (found_id, found_name or name)
        
        logging.warning(f"无法通过搜索找到产品: {name}")
        return ("", "")

    def _extract_docs_from_tree(self, tree: Any) -> List[Dict[str, str]]:
        docs: List[Dict[str, str]] = []
        seen = set()

        def walk(node: Any, inherited_product_id: str = "", inherited_category: str = "") -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item, inherited_product_id, inherited_category)
                return

            if not isinstance(node, dict):
                return

            node_title = self._pick_first_str(
                node, ["title", "name", "text", "label", "menuName", "docTitle"]
            )
            category = inherited_category
            if node_title and "children" in node:
                category = node_title

            product_id = inherited_product_id
            for key in ["productId", "product_id", "pid", "productID"]:
                candidate = self._normalize_digits(node.get(key))
                if candidate:
                    product_id = candidate
                    break

            doc_id = ""
            for key in ["docId", "doc_id", "documentId", "articleId", "pageId", "docID"]:
                candidate = self._normalize_digits(node.get(key))
                if candidate:
                    doc_id = candidate
                    break

            node_url = self._normalize_url(
                self._pick_first_str(node, ["url", "href", "path", "link", "docUrl"])
            )
            url_match = re.search(r"/document/product/(\d+)/(\d+)", node_url)
            if url_match:
                product_id = product_id or url_match.group(1)
                doc_id = doc_id or url_match.group(2)

            # 兜底：部分节点仅用 id 表示文档，只有当 URL 形态能确认时才用，避免产品节点误判
            if not doc_id and url_match:
                doc_id = self._normalize_digits(node.get("id"))

            if product_id and doc_id:
                key = (product_id, doc_id)
                if key not in seen:
                    seen.add(key)
                    docs.append(
                        {
                            "product_id": product_id,
                            "doc_id": doc_id,
                            "title": node_title or f"doc-{doc_id}",
                            "url": node_url
                            or TENCENT_DOC_URL_TEMPLATE.format(
                                product_id=product_id, doc_id=doc_id
                            ),
                            "category": category,
                        }
                    )

            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, product_id, category)

        walk(tree)
        return docs

    def _search_docs_by_keyword(
        self, product_name: str, target_product_id: str = "", limit: int = 0
    ) -> List[Dict[str, str]]:
        """通过新版搜索 API V2 获取产品下的所有文档。
        
        Args:
            product_name: 产品名称（用于搜索和过滤）
            target_product_id: 目标产品 ID，如果指定则只返回该产品的文档
            limit: 最大返回数量，0 表示不限制（默认获取全部）
        """
        docs: List[Dict[str, str]] = []
        seen: set = set()
        page = 1
        max_pages = 100  # 最多 100 页
        
        while page <= max_pages:
            self._rate_limit()
            
            # 使用 pid 过滤指定产品的文档
            filter_config = {}
            if target_product_id:
                filter_config["pid"] = target_product_id
            
            payload = {
                "action": "startup",
                "payload": {
                    "type": 7,  # 文档类型
                    "keyword": product_name,
                    "page": page,
                    "preferSynonym": True,
                    "filter": filter_config,
                    "sort": None
                }
            }
            
            try:
                encoded_name = quote(product_name, safe='')
                response = self.session.post(
                    TENCENT_SEARCH_API_V2,
                    json=payload,
                    timeout=self.timeout,
                    headers={
                        "Content-Type": "application/json",
                        "Referer": f"https://cloud.tencent.com/search/{encoded_name}/7_1",
                    }
                )
                response.raise_for_status()
                result = response.json()
            except Exception as e:
                logging.error(f"搜索腾讯云文档失败 (page={page}): {e}")
                break
            
            data = result.get("data", {})
            doc_list = data.get("list", [])
            total_pages = data.get("totalPage", 1)
            
            if not doc_list:
                break
            
            for doc in doc_list:
                url = doc.get("url", "")
                match = re.search(r"/document/product/(\d+)/(\d+)", url)
                if not match:
                    # 跳过产品首页（如 /document/product/215 没有 doc_id）
                    continue
                
                pid = match.group(1)
                doc_id = match.group(2)
                
                # 如果指定了目标产品 ID，则只保留该产品的文档
                if target_product_id and pid != target_product_id:
                    continue
                
                key = (pid, doc_id)
                if key in seen:
                    continue
                seen.add(key)
                
                docs.append({
                    "product_id": pid,
                    "doc_id": doc_id,
                    "title": doc.get("title", f"doc-{doc_id}"),
                    "url": url,
                    "category": doc.get("productName", ""),
                })
                
                if limit > 0 and len(docs) >= limit:
                    logging.info(f"已获取 {len(docs)} 篇文档，达到限制")
                    return docs
            
            # 检查是否还有更多页
            if page >= total_pages:
                break
            
            page += 1
        
        return docs

    def discover_product_docs(
        self, product_name: str, keyword: str = "", limit: int = 0
    ) -> List[Dict[str, str]]:
        """发现腾讯云某产品下的文档目录。

        流程（与 MCP 调用逻辑一致）：
        1. 用户输入产品名称（如 "私有网络"、"弹性网卡"）
        2. 用搜索 API 搜索，从结果中获取 product_id
        3. 用 filter.pid 过滤，确保只返回该产品的文档
        4. 返回 doc_id 列表供后续调用 fetch_doc 获取详情

        Args:
            product_name: 产品名称，例如 "私有网络"、"云联网"、"VPN 连接"
            keyword: 额外搜索关键词（可选）
            limit: 最大返回数量，0 表示不限制
        """
        search_query = product_name.strip()
        if keyword:
            search_query = f"{search_query} {keyword}"
        
        # 先用产品名称搜索，获取 product_id
        target_product_id, _ = self._find_product_id_by_name(product_name)
        
        logging.info(f"[腾讯云] 搜索「{search_query}」，pid={target_product_id or '无'}")

        # 使用搜索 API + filter.pid 获取文档列表
        docs = self._search_docs_by_keyword(search_query, target_product_id, limit)
        
        logging.info(f"发现腾讯云产品 {target_product_id} 文档 {len(docs)} 篇")
        return docs

    def _deep_find_string(self, obj: Any, keys: List[str]) -> str:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in obj.values():
                found = self._deep_find_string(value, keys)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._deep_find_string(item, keys)
                if found:
                    return found
        return ""

    def _deep_collect_strings(self, obj: Any, out: List[str]) -> None:
        if isinstance(obj, str):
            text = obj.strip()
            if len(text) >= 80:
                out.append(text)
            return
        if isinstance(obj, dict):
            for value in obj.values():
                self._deep_collect_strings(value, out)
            return
        if isinstance(obj, list):
            for item in obj:
                self._deep_collect_strings(item, out)

    def fetch_doc(self, doc_id: str, product_id: str = "", lang: str = "zh") -> Optional[Dict[str, str]]:
        """通过 getDocPageDetail API 获取腾讯云单篇文档。

        Args:
            doc_id: 文档 ID（URL 中 /product/{pid}/{doc_id} 的后者）
            product_id: 产品 ID（可选，用于构建链接和设置 Referer）
            lang: 语言，默认 zh
        """
        normalized_doc_id = self._normalize_digits(doc_id)
        normalized_product_id = self._normalize_digits(product_id)
        if not normalized_doc_id:
            logging.error(f"无效的腾讯云 doc_id: {doc_id}")
            return None

        self._rate_limit()
        referer = "https://cloud.tencent.com/"
        if normalized_product_id:
            referer = TENCENT_DOC_URL_TEMPLATE.format(
                product_id=normalized_product_id,
                doc_id=normalized_doc_id,
            )

        payload = {
            "action": "getDocPageDetail",
            "payload": {
                "id": normalized_doc_id,
                "lang": lang,
                "isPreview": False,
                "isFromClient": True,
            },
        }

        try:
            response = self.session.post(
                TENCENT_DOC_DETAIL_API,
                json=payload,
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Referer": referer,
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                },
            )
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            logging.error(f"获取腾讯云文档失败 doc_id={normalized_doc_id}: {e}")
            return None

        if isinstance(result, dict):
            code = result.get("code")
            if code not in (None, 0, 200, "0", "200"):
                logging.warning(f"腾讯云文档 API 返回非成功状态: code={code}, doc_id={normalized_doc_id}")
            data = result.get("data")
            if data is None:
                data = result.get("result")
            if data is None:
                data = result
        else:
            logging.error(f"腾讯云文档 API 返回非 JSON 对象: doc_id={normalized_doc_id}")
            return None

        title = self._deep_find_string(data, ["title", "name", "docTitle", "pageTitle"])
        html_content = self._deep_find_string(data, ["content", "html", "docContent", "body"])
        if not html_content:
            candidates: List[str] = []
            self._deep_collect_strings(data, candidates)
            if candidates:
                html_content = max(candidates, key=len)

        if not html_content:
            logging.error(f"腾讯云文档内容为空: doc_id={normalized_doc_id}")
            return None

        if "<" in html_content and ">" in html_content:
            soup = BeautifulSoup(html_content, "lxml")
            text = soup.get_text(separator="\n", strip=True)
            html = html_content
        else:
            text = html_content
            html = ""

        url = self._normalize_url(self._deep_find_string(data, ["url", "link", "docUrl"]))
        url_match = re.search(r"/document/product/(\d+)/(\d+)", url)
        if url_match:
            normalized_product_id = normalized_product_id or url_match.group(1)
            normalized_doc_id = normalized_doc_id or url_match.group(2)

        if not normalized_product_id:
            inferred_pid = self._normalize_digits(
                self._deep_find_string(data, ["productId", "product_id", "pid"])
            )
            normalized_product_id = inferred_pid

        if not url:
            if normalized_product_id:
                url = TENCENT_DOC_URL_TEMPLATE.format(
                    product_id=normalized_product_id,
                    doc_id=normalized_doc_id,
                )
            else:
                url = f"https://cloud.tencent.com/document/{normalized_doc_id}"

        return {
            "title": title or f"doc-{normalized_doc_id}",
            "text": text,
            "html": html,
            "url": url,
            "doc_id": normalized_doc_id,
            "product_id": normalized_product_id,
            "last_modified": self._deep_find_string(
                data,
                ["lastModified", "lastModifiedTime", "updateTime", "updatedAt", "publishTime"],
            ),
        }


"""火山云文档爬虫模块

使用 hotArticles API 获取文档列表，支持：
1. 根据产品名称搜索对应 LibID
2. 获取产品文档列表
3. 获取文档详情（从页面 SSR 数据提取）
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


# 已知的火山云产品 LibID 映射（用于初始搜索）
VOLCANO_KNOWN_LIBS = {
    "6401": "私有网络",
    "6405": "云企业网",
    "6454": "内容分发网络",
    "6404": "NAT网关",
    "6427": "飞连",
    "6638": "证书中心",
    "6737": "全球加速",
    "6752": "WebRTC 传输网络",
}

VOLCANO_SEARCH_ALL_API = "https://www.volcengine.com/api/search/searchAll"


class VolcanoDocCrawler:
    """火山云文档爬虫 - 基于 hotArticles API"""

    def __init__(self, request_delay: float = 0.3, timeout: int = 30):
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "accept": "application/json",
            "content-type": "application/json",
            "origin": "https://www.volcengine.com",
            "referer": "https://www.volcengine.com/docs",
            "x-use-bff-version": "1",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })
        self.last_request_time = 0.0
        # 缓存：产品名称 -> LibID
        self._lib_id_cache: Dict[str, str] = {}

    @staticmethod
    def _pick_first_str(node: Dict[str, Any], keys: List[str]) -> str:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _normalize_doc_url(url: str) -> str:
        text = (url or "").strip()
        if not text:
            return ""
        if text.startswith("//"):
            return f"https:{text}"
        if text.startswith("/"):
            return f"https://www.volcengine.com{text}"
        return text

    @staticmethod
    def _extract_doc_ids(url: str) -> Tuple[str, str]:
        match = re.search(r"/docs/(\d+)/(\d+)", (url or "").strip())
        if not match:
            return "", ""
        return match.group(1), match.group(2)

    def _to_doc_entry(self, node: Dict[str, Any]) -> Optional[Dict[str, str]]:
        url = self._normalize_doc_url(
            self._pick_first_str(node, ["Url", "URL", "url", "DocURL", "DocUrl", "Link", "link"])
        )
        lib_id, doc_id = self._extract_doc_ids(url)
        if not lib_id or not doc_id:
            return None

        title = self._pick_first_str(
            node,
            ["Name", "name", "Title", "title", "DocName", "docName"],
        )
        return {
            "doc_id": doc_id,
            "lib_id": lib_id,
            "name": title or f"doc-{doc_id}",
            "url": url,
        }

    def _extract_doc_entries_from_search_payload(self, data: Any) -> List[Dict[str, str]]:
        """从 searchAll API 返回数据中提取文档列表
        
        searchAll 返回结构: Result.List[0].DocList[]
        每个文档包含: Url, Title, ID 等字段
        """
        candidates: List[Any] = []
        
        # searchAll 的特殊结构
        result = data.get("Result", {})
        list_items = result.get("List", [])
        for item in list_items:
            if isinstance(item, dict) and "DocList" in item:
                doc_list = item.get("DocList", [])
                if isinstance(doc_list, list):
                    candidates.extend(doc_list)
        
        # 通用兜底：尝试常见路径
        if not candidates:
            known_paths = [
                ("Result", "List"),
                ("Result", "DocList"),
                ("Data", "List"),
                ("data", "list"),
            ]
            for path in known_paths:
                cur = data
                ok = True
                for key in path:
                    if isinstance(cur, dict) and key in cur:
                        cur = cur[key]
                    else:
                        ok = False
                        break
                if ok and isinstance(cur, list):
                    candidates.extend(cur)
                    break

        docs: List[Dict[str, str]] = []
        seen = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            doc = self._to_doc_entry(item)
            if not doc:
                continue
            key = (doc["lib_id"], doc["doc_id"])
            if key in seen:
                continue
            seen.add(key)
            docs.append(doc)
        return docs

    def _rate_limit(self) -> None:
        """请求频率限制"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self.last_request_time = time.time()

    def _find_lib_id_by_name(self, product_name: str) -> Optional[Tuple[str, str]]:
        """根据产品名称查找 LibID
        
        Args:
            product_name: 产品名称，如 "私有网络"、"云企业网"
            
        Returns:
            (lib_id, actual_name) 元组，或 None
        """
        # 先检查缓存
        if product_name in self._lib_id_cache:
            lib_id = self._lib_id_cache[product_name]
            return (lib_id, product_name)
        
        # 先从已知映射中查找
        for lib_id, name in VOLCANO_KNOWN_LIBS.items():
            if product_name in name or name in product_name:
                self._lib_id_cache[product_name] = lib_id
                return (lib_id, name)
        
        # 使用 searchAll API 搜索产品
        docs = self.search_docs(product_name, limit=20)
        if docs:
            # 取第一个匹配的文档
            first_doc = docs[0]
            lib_id = first_doc["lib_id"]
            self._lib_id_cache[product_name] = lib_id
            logging.info(f"通过 searchAll 找到产品 '{product_name}' -> LibID: {lib_id}")
            return (lib_id, product_name)
        
        logging.warning(f"未找到产品 '{product_name}' 对应的 LibID")
        return None

    def resolve_lib_id(self, product_name: str) -> str:
        """解析产品名称对应的 LibID，解析失败返回空字符串。"""
        result = self._find_lib_id_by_name(product_name)
        if not result:
            return ""
        return result[0]

    def search_docs(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        lib_id: str = "",
        referer_lib_id: str = "6455",
    ) -> List[Dict[str, str]]:
        """使用 searchAll 接口搜索火山云文档（支持自动分页）。

        Args:
            query: 搜索关键词
            limit: 返回上限（0 表示不限制，会自动分页获取所有结果）
            offset: 起始偏移量
            lib_id: 过滤指定 LibID（可选）
            referer_lib_id: 请求头 Referer 中使用的文档库 ID
        """
        query_text = (query or "").strip()
        if not query_text:
            return []

        # API 单次最多返回 50 条（服务端限制）
        PAGE_SIZE = 50
        all_docs = []
        current_offset = offset
        
        # 如果 limit=0 或 limit>50，需要分页
        need_pagination = (limit == 0 or limit > PAGE_SIZE)
        
        while True:
            self._rate_limit()
            params = {
                "Caller": "doc",
                "Did": "84565665",
                "Uid": "0",
                "UUID": "11_000J3vkCWUxPtPrMwlQ3ilQxfn2UtC",
                "UidType": "14",
                "Query": query_text,
                "Category1": "",
                "Offset": current_offset,
                "Limit": 10000,  # 请求最大值，但服务端只返回 50
                "Type": "doc",
                "UserID": "-1",
            }
            headers = {
                "accept": "application/json",
                "referer": f"https://www.volcengine.com/docs/{referer_lib_id}?lang=zh",
                "x-use-bff-version": "1",
            }

            try:
                resp = self.session.get(
                    VOLCANO_SEARCH_ALL_API,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logging.error(f"searchAll 查询失败(query={query_text}, offset={current_offset}): {e}")
                break

            page_docs = self._extract_doc_entries_from_search_payload(data)
            
            if not page_docs:
                # 没有更多结果
                break
            
            all_docs.extend(page_docs)
            
            # 判断是否继续分页
            if not need_pagination:
                # 不需要分页，获取一页即可
                break
            
            if limit > 0 and len(all_docs) >= limit:
                # 已达到用户指定的 limit
                break
            
            if len(page_docs) < PAGE_SIZE:
                # 本页不足 50 条，说明已到最后一页
                break
            
            # 继续下一页
            current_offset += PAGE_SIZE
            logging.info(f"[火山云] 分页获取下一页 (offset={current_offset})")

        # 过滤 lib_id
        if lib_id:
            all_docs = [doc for doc in all_docs if doc.get("lib_id") == str(lib_id)]

        # 去重（防止分页重复）
        seen = set()
        unique_docs = []
        for doc in all_docs:
            key = (doc.get("lib_id"), doc.get("doc_id"))
            if key not in seen:
                seen.add(key)
                unique_docs.append(doc)

        logging.info(
            f"[火山云] searchAll(query={query_text}, lib_id={lib_id or '-'}) 共返回 {len(unique_docs)} 篇文档"
        )
        
        return unique_docs[:limit] if limit > 0 else unique_docs

    def discover_product_docs(
        self, product_name: str, limit: int = 0
    ) -> List[Dict[str, str]]:
        """发现产品的所有文档
        
        Args:
            product_name: 产品名称，如 "私有网络"
            limit: 最大返回数量，0 表示不限制
            
        Returns:
            文档列表，每项包含 doc_id, lib_id, name, url
        """
        # 1. 查找 LibID
        result = self._find_lib_id_by_name(product_name)
        if not result:
            return []
        
        lib_id, actual_name = result
        logging.info(f"[火山云] 搜索「{product_name}」，LibID={lib_id}")
        
        # 2. 调用 hotArticles 获取文档列表
        self._rate_limit()
        try:
            # 设置较大的 Limit 获取尽可能多的文档
            fetch_limit = limit if limit > 0 else 200
            payload = {
                "LibIDs": [lib_id],
                "Limit": fetch_limit,
            }
            resp = self.session.post(
                "https://www.volcengine.com/api/search/hotArticles",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            
            result = data.get("Result", {})
            docs = result.get("List", [])
            
            doc_list = []
            for doc in docs:
                url = doc.get("URL", "")
                name = doc.get("Name", "")
                
                # 从 URL 提取 doc_id: https://www.volcengine.com/docs/6401/69467
                match = re.search(r"/docs/(\d+)/(\d+)", url)
                if match:
                    doc_lib_id, doc_id = match.groups()
                    doc_list.append({
                        "doc_id": doc_id,
                        "lib_id": doc_lib_id,
                        "name": name,
                        "url": url,
                    })
            
            logging.info(f"发现火山云产品 {lib_id} 文档 {len(doc_list)} 篇")
            return doc_list
            
        except Exception as e:
            logging.error(f"获取火山云文档列表失败: {e}")
            return []

    def fetch_doc(self, lib_id: str, doc_id: str) -> Optional[Dict]:
        """获取单篇火山云文档详情
        
        通过 getDocDetail API 获取文档内容
        
        Args:
            lib_id: 产品 LibID
            doc_id: 文档 ID
            
        Returns:
            包含 title, text, html, url 的字典
        """
        self._rate_limit()
        api_url = "https://www.volcengine.com/api/doc/getDocDetail"
        params = {
            "LibraryID": lib_id,
            "DocumentID": doc_id,
            "type": "online",
        }
        
        try:
            resp = self.session.get(api_url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            
            result = data.get("Result", {})
            if not result:
                logging.warning(f"火山云文档 {lib_id}/{doc_id} API 返回空")
                return None
            
            title = result.get("Title", "")
            
            # 优先使用 MDContent (Markdown 格式)
            text = result.get("MDContent", "")
            
            # 如果 MDContent 为空，尝试从 Content (富文本 JSON) 提取
            if not text:
                content_json = result.get("Content", "")
                if content_json:
                    text = self._extract_text_from_content(content_json)
            
            if not text:
                logging.warning(f"火山云文档 {lib_id}/{doc_id} 内容为空")
                return None
            
            doc_url = f"https://www.volcengine.com/docs/{lib_id}/{doc_id}"
            return {
                "title": title or f"doc-{doc_id}",
                "text": text,
                "html": "",
                "url": doc_url,
                "lib_id": lib_id,
                "doc_id": doc_id,
            }
            
        except Exception as e:
            logging.error(f"获取火山云文档失败 {lib_id}/{doc_id}: {e}")
            return None

    def _extract_text_from_content(self, content_json: str) -> str:
        """从火山云富文本 JSON 格式提取纯文本"""
        try:
            data = json.loads(content_json)
            texts = []
            data_obj = data.get("data", {})
            for key in sorted(data_obj.keys(), key=lambda x: int(x) if x.isdigit() else 0):
                value = data_obj[key]
                if isinstance(value, dict) and "ops" in value:
                    for op in value["ops"]:
                        insert = op.get("insert", "")
                        if isinstance(insert, str) and insert and insert != "*":
                            texts.append(insert)
            return "".join(texts)
        except Exception as e:
            logging.debug(f"解析富文本失败: {e}")
            return ""

    def fetch_docs_batch(
        self, lib_id: str, doc_ids: List[str], max_pages: int = 0
    ) -> List[Dict]:
        """批量获取火山云文档
        
        Args:
            lib_id: 产品 LibID
            doc_ids: 文档 ID 列表
            max_pages: 最大获取数量，0 表示不限制
        """
        docs = []
        for doc_id in doc_ids:
            if max_pages > 0 and len(docs) >= max_pages:
                break
            doc = self.fetch_doc(lib_id, doc_id)
            if doc:
                docs.append(doc)
                logging.info(f"已获取火山云文档 {len(docs)}/{len(doc_ids)}: {doc['title']}")
        return docs

"""MCP 服务依赖与通用工具。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get_config
from .crawler import alias_to_url, url_to_alias
from .models import Document
from .storage import DocumentStorage
from .summarizer import AISummarizer, DashScopeAdapter
from .utils import compute_content_hash


class AppServices:
    """为 MCP 工具提供共享依赖与通用解析方法。"""

    def __init__(self):
        self._storage: Optional[DocumentStorage] = None
        self._config = None
        self._summarizer: Optional[AISummarizer] = None

    def get_config(self):
        if self._config is None:
            self._config = get_config()
        return self._config

    def get_storage(self) -> DocumentStorage:
        if self._storage is None:
            config = self.get_config()
            db_path = config.get("storage.database", "./data/aliyun_docs.db")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._storage = DocumentStorage(f"sqlite:///{db_path}")
            self._storage.init_db()
        return self._storage

    def get_summarizer(self) -> AISummarizer:
        if self._summarizer is None:
            config = self.get_config()
            adapter = DashScopeAdapter(
                model=config.get("llm.model", "qwen-turbo"),
                api_key=config.get("llm.api_key", ""),
            )
            self._summarizer = AISummarizer(adapter)
        return self._summarizer

    @staticmethod
    def build_scope_urls(aliases: List[str]) -> set[str]:
        """将阿里云 alias/URL 输入统一转换为文档 URL 集合。"""
        scope_urls: set[str] = set()
        for raw in aliases:
            if not raw:
                continue
            value = raw.strip()
            if not value:
                continue
            if value.startswith("http"):
                try:
                    value = url_to_alias(value)
                except Exception:
                    continue
            if not value.startswith("/"):
                value = f"/{value}"
            scope_urls.add(alias_to_url(value))
        return scope_urls

    @staticmethod
    def extract_digits(value: str) -> str:
        match = re.search(r"(\d+)", (value or "").strip())
        return match.group(1) if match else ""

    @classmethod
    def parse_tencent_doc_ref(
        cls,
        doc_ref: str,
        fallback_product_id: str = "",
    ) -> tuple[str, str]:
        """解析腾讯云文档引用，返回 (product_id, doc_id)。"""
        product_id = cls.extract_digits(fallback_product_id)
        raw = (doc_ref or "").strip()
        if not raw:
            return product_id, ""

        if raw.startswith("http"):
            match = re.search(r"/document/product/(\d+)/(\d+)", raw)
            if not match:
                return product_id, ""
            if not product_id:
                product_id = match.group(1)
            return product_id, match.group(2)

        return product_id, cls.extract_digits(raw)

    @staticmethod
    def tencent_doc_url(product_id: str, doc_id: str) -> str:
        return f"https://cloud.tencent.com/document/product/{product_id}/{doc_id}"

    @classmethod
    def normalize_tencent_doc_url(cls, url: str) -> str:
        text = (url or "").strip()
        match = re.search(r"/document/product/(\d+)/(\d+)", text)
        if match:
            return cls.tencent_doc_url(match.group(1), match.group(2))
        return text

    @classmethod
    def build_tencent_scope_urls(
        cls,
        doc_refs: List[str],
        default_product_id: str = "",
    ) -> set[str]:
        scope_urls: set[str] = set()
        for ref in doc_refs:
            pid, doc_id = cls.parse_tencent_doc_ref(ref, default_product_id)
            if pid and doc_id:
                scope_urls.add(cls.tencent_doc_url(pid, doc_id))
                continue
            normalized = cls.normalize_tencent_doc_url(ref)
            if normalized.startswith("https://cloud.tencent.com/document/product/"):
                scope_urls.add(normalized)
        return scope_urls

    @staticmethod
    def parse_datetime_value(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value

        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 1_000_000_000_000:
                timestamp = timestamp / 1000
            try:
                return datetime.fromtimestamp(timestamp)
            except Exception:
                return None

        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            try:
                timestamp = int(text)
                if timestamp > 1_000_000_000_000:
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp)
            except Exception:
                return None

        try:
            iso_text = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(iso_text)
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            pass

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    @classmethod
    def build_tencent_document(cls, doc_detail: Dict[str, Any]) -> Optional[Document]:
        title = str(doc_detail.get("title", "")).strip()
        url = cls.normalize_tencent_doc_url(str(doc_detail.get("url", "")).strip())
        text = str(doc_detail.get("text", "")).strip()
        if not url or not text:
            return None

        return Document(
            url=url,
            title=title or "untitled",
            content=text,
            content_hash=compute_content_hash(text),
            last_modified=cls.parse_datetime_value(doc_detail.get("last_modified")),
            metadata={
                "cloud": "tencent",
                "product_id": str(doc_detail.get("product_id", "")).strip(),
                "doc_id": str(doc_detail.get("doc_id", "")).strip(),
            },
        )


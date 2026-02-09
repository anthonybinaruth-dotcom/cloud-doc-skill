"""HTTP API 服务 - 让别人通过接口直接使用文档监控助手"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .config import get_config
from .crawler import DocumentCrawler
from .detector import ChangeDetector
from .storage import DocumentStorage, ScanRecordDB, ChangeDB, DocumentDB
from .summarizer import AISummarizer, DashScopeAdapter
from .utils import compute_content_hash

app = FastAPI(
    title="阿里云文档监控助手",
    description="自动追踪阿里云文档更新，使用AI生成变更摘要",
    version="0.1.0",
)

# 全局实例
_storage = None
_config = None
_summarizer = None


def _get_config():
    global _config
    if _config is None:
        _config = get_config()
    return _config


def _get_storage():
    global _storage
    if _storage is None:
        config = _get_config()
        db_path = config.get("storage.database", "./data/aliyun_docs.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _storage = DocumentStorage(f"sqlite:///{db_path}")
        _storage.init_db()
    return _storage


def _get_summarizer():
    global _summarizer
    if _summarizer is None:
        config = _get_config()
        adapter = DashScopeAdapter(
            model=config.get("llm.model", "qwen-turbo"),
            api_key=config.get("llm.api_key", ""),
        )
        _summarizer = AISummarizer(adapter)
    return _summarizer


# ========== 数据模型 ==========

class CheckResponse(BaseModel):
    status: str
    documents_scanned: int
    changes: dict

class ChangeItem(BaseModel):
    document_title: str
    document_url: str
    change_type: str
    summary: str
    detected_at: str

class StatsResponse(BaseModel):
    total_documents: int
    total_scans: int
    total_changes: int
    last_scan: Optional[dict] = None


# ========== API 接口 ==========

@app.get("/")
def root():
    """服务状态检查"""
    return {"service": "阿里云文档监控助手", "status": "running", "version": "0.1.0"}


@app.post("/check", response_model=CheckResponse)
def trigger_check(max_pages: int = Query(default=50, description="最大爬取页数")):
    """手动触发一次文档检查"""
    try:
        config = _get_config()
        storage = _get_storage()

        crawler = DocumentCrawler(
            base_url=config.get("crawler.base_url", "https://help.aliyun.com"),
            request_delay=config.get("crawler.request_delay", 1.0),
        )
        detector = ChangeDetector()

        old_docs = storage.get_all_documents()
        new_docs = crawler.crawl_site(config.get("crawler.base_url"), max_pages=max_pages)

        for doc in new_docs:
            doc_id = storage.save_document(doc)
            storage.save_version(doc_id, doc.content, doc.content_hash)

        report = detector.detect_changes(old_docs, new_docs)

        return CheckResponse(
            status="completed",
            documents_scanned=len(new_docs),
            changes={
                "added": len(report.added),
                "modified": len(report.modified),
                "deleted": len(report.deleted),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/changes", response_model=List[ChangeItem])
def get_recent_changes(days: int = Query(default=7, description="查询最近N天的变更")):
    """获取最近的文档变更记录"""
    try:
        storage = _get_storage()
        session = storage.get_session()
        try:
            cutoff = datetime.now() - timedelta(days=days)
            changes = (
                session.query(ChangeDB)
                .filter(ChangeDB.created_at >= cutoff)
                .order_by(ChangeDB.created_at.desc())
                .all()
            )

            results = []
            for change in changes:
                doc = session.query(DocumentDB).filter_by(id=change.document_id).first()
                results.append(ChangeItem(
                    document_title=doc.title if doc else "未知",
                    document_url=doc.url if doc else "",
                    change_type=change.change_type,
                    summary=change.summary or "",
                    detected_at=change.created_at.isoformat(),
                ))
            return results
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", response_model=StatsResponse)
def get_statistics():
    """获取监控统计信息"""
    try:
        storage = _get_storage()
        session = storage.get_session()
        try:
            total_docs = session.query(DocumentDB).count()
            total_scans = session.query(ScanRecordDB).count()
            total_changes = session.query(ChangeDB).count()

            last_scan = (
                session.query(ScanRecordDB)
                .order_by(ScanRecordDB.started_at.desc())
                .first()
            )

            return StatsResponse(
                total_documents=total_docs,
                total_scans=total_scans,
                total_changes=total_changes,
                last_scan={
                    "started_at": last_scan.started_at.isoformat(),
                    "status": last_scan.status,
                } if last_scan else None,
            )
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/summarize")
def summarize_text(title: str, old_content: str, new_content: str):
    """直接传入新旧内容，生成变更摘要"""
    try:
        from .models import Document, DocumentChange, ChangeType
        import difflib

        diff = "\n".join(difflib.unified_diff(
            old_content.splitlines(), new_content.splitlines(),
            fromfile="旧版本", tofile="新版本", lineterm=""
        ))

        doc = Document(
            url="", title=title, content=new_content,
            content_hash=compute_content_hash(new_content),
        )
        change = DocumentChange(
            document=doc,
            old_content_hash=compute_content_hash(old_content),
            new_content_hash=doc.content_hash,
            diff=diff,
            change_type=ChangeType.MAJOR,
        )

        summarizer = _get_summarizer()
        summary = summarizer.summarize_change(change)
        return {"title": title, "summary": summary, "diff": diff}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

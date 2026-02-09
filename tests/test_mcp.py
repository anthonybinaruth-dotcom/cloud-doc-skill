"""MCP集成测试"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.storage import DocumentStorage, DocumentDB, ScanRecordDB, ChangeDB


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "test.db"
    s = DocumentStorage(f"sqlite:///{db_path}")
    s.init_db()
    return s


class TestMCPTools:
    """测试各个工具接口"""

    def test_get_statistics_empty_db(self, storage):
        """测试空数据库的统计信息"""
        session = storage.get_session()
        try:
            total_docs = session.query(DocumentDB).count()
            total_scans = session.query(ScanRecordDB).count()
            assert total_docs == 0
            assert total_scans == 0
        finally:
            session.close()

    def test_get_statistics_with_data(self, storage):
        """测试有数据时的统计信息"""
        from src.models import Document
        doc = Document(
            url="https://help.aliyun.com/test",
            title="测试",
            content="内容",
            content_hash="hash",
        )
        storage.save_document(doc)
        storage.save_scan_record(started_at=datetime.now(), status="completed")

        session = storage.get_session()
        try:
            total_docs = session.query(DocumentDB).count()
            total_scans = session.query(ScanRecordDB).count()
            assert total_docs == 1
            assert total_scans == 1
        finally:
            session.close()


class TestParameterValidation:
    """测试参数验证"""

    def test_scan_history_limit(self, storage):
        """测试扫描历史限制"""
        # 创建多条记录
        for i in range(5):
            storage.save_scan_record(started_at=datetime.now(), status="completed")

        session = storage.get_session()
        try:
            scans = (
                session.query(ScanRecordDB)
                .order_by(ScanRecordDB.started_at.desc())
                .limit(3)
                .all()
            )
            assert len(scans) == 3
        finally:
            session.close()


class TestErrorHandling:
    """测试错误处理"""

    def test_get_document_nonexistent(self, storage):
        result = storage.get_document("https://nonexistent.com")
        assert result is None

    def test_get_latest_version_no_versions(self, storage):
        from src.models import Document
        doc = Document(
            url="https://help.aliyun.com/no-version",
            title="无版本",
            content="内容",
            content_hash="hash",
        )
        doc_id = storage.save_document(doc)
        result = storage.get_latest_version(doc_id)
        assert result is None

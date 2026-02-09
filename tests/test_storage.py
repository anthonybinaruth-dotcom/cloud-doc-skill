"""存储模块单元测试"""

from datetime import datetime

import pytest

from src.models import Document
from src.storage import DocumentStorage


@pytest.fixture
def storage(tmp_path):
    """创建测试用存储实例（使用临时数据库）"""
    db_path = tmp_path / "test.db"
    s = DocumentStorage(f"sqlite:///{db_path}")
    s.init_db()
    return s


@pytest.fixture
def sample_doc():
    """创建测试用文档"""
    return Document(
        url="https://help.aliyun.com/test-doc",
        title="测试文档",
        content="这是测试内容",
        content_hash="abc123",
        last_modified=datetime(2026, 1, 1),
        crawled_at=datetime.now(),
    )


class TestDocumentSaveAndRetrieve:
    """测试文档保存和检索"""

    def test_save_and_get_document(self, storage, sample_doc):
        doc_id = storage.save_document(sample_doc)
        assert doc_id > 0

        retrieved = storage.get_document(sample_doc.url)
        assert retrieved is not None
        assert retrieved.url == sample_doc.url
        assert retrieved.title == sample_doc.title
        assert retrieved.content_hash == sample_doc.content_hash

    def test_save_duplicate_url_updates(self, storage, sample_doc):
        id1 = storage.save_document(sample_doc)

        # 修改标题后再次保存
        sample_doc.title = "更新后的标题"
        sample_doc.content_hash = "def456"
        id2 = storage.save_document(sample_doc)

        assert id1 == id2  # 应该是同一条记录

        retrieved = storage.get_document(sample_doc.url)
        assert retrieved.title == "更新后的标题"

    def test_get_nonexistent_document(self, storage):
        result = storage.get_document("https://nonexistent.com")
        assert result is None

    def test_get_all_documents(self, storage):
        docs = [
            Document(url=f"https://help.aliyun.com/doc{i}", title=f"文档{i}",
                     content=f"内容{i}", content_hash=f"hash{i}")
            for i in range(3)
        ]
        for doc in docs:
            storage.save_document(doc)

        all_docs = storage.get_all_documents()
        assert len(all_docs) == 3


class TestVersionHistory:
    """测试版本历史管理"""

    def test_save_and_get_version(self, storage, sample_doc):
        doc_id = storage.save_document(sample_doc)
        version_id = storage.save_version(doc_id, "版本1内容", "hash_v1")
        assert version_id > 0

        content = storage.get_latest_version(doc_id)
        assert content == "版本1内容"

    def test_version_auto_increment(self, storage, sample_doc):
        doc_id = storage.save_document(sample_doc)

        storage.save_version(doc_id, "版本1", "hash1")
        storage.save_version(doc_id, "版本2", "hash2")
        storage.save_version(doc_id, "版本3", "hash3")

        content = storage.get_latest_version(doc_id)
        assert content == "版本3"

    def test_get_latest_version_no_versions(self, storage, sample_doc):
        doc_id = storage.save_document(sample_doc)
        content = storage.get_latest_version(doc_id)
        assert content is None


class TestScanRecords:
    """测试扫描记录保存"""

    def test_save_scan_record(self, storage):
        scan_id = storage.save_scan_record(started_at=datetime.now(), status="running")
        assert scan_id > 0

    def test_update_scan_record(self, storage):
        scan_id = storage.save_scan_record(started_at=datetime.now(), status="running")
        storage.update_scan_record(
            scan_id=scan_id,
            completed_at=datetime.now(),
            status="completed",
            documents_scanned=10,
            changes_detected=3,
        )
        # 验证更新成功（通过session查询）
        session = storage.get_session()
        try:
            from src.storage import ScanRecordDB
            scan = session.query(ScanRecordDB).filter_by(id=scan_id).first()
            assert scan.status == "completed"
            assert scan.documents_scanned == 10
        finally:
            session.close()


class TestDatabaseTransactions:
    """测试数据库事务"""

    def test_save_change_record(self, storage, sample_doc):
        doc_id = storage.save_document(sample_doc)
        scan_id = storage.save_scan_record(started_at=datetime.now(), status="running")

        change_id = storage.save_change(
            scan_id=scan_id,
            document_id=doc_id,
            change_type="modified",
            diff="- old\n+ new",
            summary="测试变更",
        )
        assert change_id > 0

    def test_save_notification_record(self, storage):
        scan_id = storage.save_scan_record(started_at=datetime.now(), status="running")
        notif_id = storage.save_notification(
            scan_id=scan_id, channel="webhook", status="pending"
        )
        assert notif_id > 0

        storage.update_notification(
            notification_id=notif_id,
            status="sent",
            sent_at=datetime.now(),
        )

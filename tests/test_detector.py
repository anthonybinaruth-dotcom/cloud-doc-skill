"""变更检测模块单元测试"""

from datetime import datetime

import pytest

from src.detector import ChangeDetector
from src.models import ChangeType, Document


@pytest.fixture
def detector():
    return ChangeDetector()


def make_doc(url, title="测试", content="内容", content_hash=None):
    """辅助函数：创建测试文档"""
    from src.utils import compute_content_hash
    if content_hash is None:
        content_hash = compute_content_hash(content)
    return Document(
        url=url, title=title, content=content,
        content_hash=content_hash, crawled_at=datetime.now(),
    )


class TestDetectNewDocuments:
    """测试新增文档检测"""

    def test_detect_added_documents(self, detector):
        old_docs = []
        new_docs = [make_doc("https://example.com/doc1", content="新文档")]

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.added) == 1
        assert len(report.modified) == 0
        assert len(report.deleted) == 0

    def test_detect_multiple_added(self, detector):
        old_docs = [make_doc("https://example.com/existing")]
        new_docs = [
            make_doc("https://example.com/existing"),
            make_doc("https://example.com/new1", content="新1"),
            make_doc("https://example.com/new2", content="新2"),
        ]

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.added) == 2


class TestDetectModifiedDocuments:
    """测试修改文档检测"""

    def test_detect_modified_document(self, detector):
        old_docs = [make_doc("https://example.com/doc1", content="旧内容")]
        new_docs = [make_doc("https://example.com/doc1", content="新内容")]

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.modified) == 1
        assert report.modified[0].document.url == "https://example.com/doc1"

    def test_no_change_same_content(self, detector):
        doc = make_doc("https://example.com/doc1", content="相同内容")
        old_docs = [doc]
        new_docs = [make_doc("https://example.com/doc1", content="相同内容")]

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.modified) == 0


class TestDetectDeletedDocuments:
    """测试删除文档检测"""

    def test_detect_deleted_document(self, detector):
        old_docs = [make_doc("https://example.com/doc1")]
        new_docs = []

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.deleted) == 1

    def test_detect_multiple_deleted(self, detector):
        old_docs = [
            make_doc("https://example.com/doc1"),
            make_doc("https://example.com/doc2"),
        ]
        new_docs = []

        report = detector.detect_changes(old_docs, new_docs)
        assert len(report.deleted) == 2


class TestComputeDiff:
    """测试差异计算功能"""

    def test_compute_diff_basic(self, detector):
        diff = detector.compute_diff("旧内容\n第二行", "新内容\n第二行")
        assert "旧内容" in diff or "-" in diff
        assert "新内容" in diff or "+" in diff

    def test_compute_diff_identical(self, detector):
        diff = detector.compute_diff("相同内容", "相同内容")
        assert diff == ""  # 相同内容应该没有差异

    def test_compute_diff_multiline(self, detector):
        old = "第一行\n第二行\n第三行"
        new = "第一行\n修改的第二行\n第三行\n新增第四行"
        diff = detector.compute_diff(old, new)
        assert len(diff) > 0


class TestCategorizeChange:
    """测试变更分类功能"""

    def test_minor_change(self, detector):
        diff = "+新增一行\n-删除一行"
        result = detector.categorize_change(diff)
        assert result == ChangeType.MINOR

    def test_major_change(self, detector):
        # 生成超过10行变更
        lines = [f"+新增行{i}" for i in range(15)]
        diff = "\n".join(lines)
        result = detector.categorize_change(diff)
        assert result == ChangeType.MAJOR

    def test_structural_change(self, detector):
        diff = "+## 新章节标题\n+新内容"
        result = detector.categorize_change(diff)
        assert result == ChangeType.STRUCTURAL

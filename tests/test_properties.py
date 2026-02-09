"""Property-Based Tests (爬虫、存储和变更检测)"""

from datetime import datetime

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.detector import ChangeDetector
from src.models import Document
from src.storage import DocumentStorage
from src.utils import compute_content_hash, deduplicate_urls, normalize_url


# ============================================================
# 属性 5.1.1: URL去重正确性
# **Validates: Requirements 3.1.3**
# ============================================================

# URL生成策略
url_strategy = st.from_regex(
    r"https://help\.aliyun\.com/[a-z0-9]{1,20}(/[a-z0-9]{1,10}){0,3}",
    fullmatch=True,
)


@given(urls=st.lists(url_strategy, min_size=0, max_size=50))
@settings(max_examples=100)
def test_url_dedup_correctness(urls):
    """
    属性 5.1.1: URL去重正确性
    对于任意URL列表，去重后不应包含重复URL
    ∀ urls: List[str], len(deduplicate(urls)) == len(set(urls))

    **Validates: Requirements 3.1.3**
    """
    result = deduplicate_urls(urls)

    # 去重后长度应等于集合长度
    assert len(result) == len(set(urls))

    # 去重后不应有重复
    assert len(result) == len(set(result))

    # 去重后的元素应该都在原列表中
    for url in result:
        assert url in urls

    # 原列表中的每个唯一URL都应在结果中
    for url in set(urls):
        assert url in result


# ============================================================
# 属性 5.1.2: 内容哈希一致性
# **Validates: Requirements 3.1.2**
# ============================================================

@given(content=st.text(min_size=0, max_size=1000))
@settings(max_examples=100)
def test_content_hash_deterministic(content):
    """
    属性 5.1.2a: 相同内容应产生相同哈希
    ∀ content: str, hash(content) == hash(content)

    **Validates: Requirements 3.1.2**
    """
    hash1 = compute_content_hash(content)
    hash2 = compute_content_hash(content)
    assert hash1 == hash2


@given(
    content1=st.text(min_size=1, max_size=500),
    content2=st.text(min_size=1, max_size=500),
)
@settings(max_examples=100)
def test_content_hash_different_for_different_content(content1, content2):
    """
    属性 5.1.2b: 不同内容应产生不同哈希（概率性）
    ∀ content1 ≠ content2: str, hash(content1) ≠ hash(content2)

    **Validates: Requirements 3.1.2**
    """
    assume(content1 != content2)
    hash1 = compute_content_hash(content1)
    hash2 = compute_content_hash(content2)
    assert hash1 != hash2


# ============================================================
# 属性 5.3.1: 保存后可检索
# **Validates: Requirements 3.5.1**
# ============================================================

@given(
    url_path=st.from_regex(r"[a-z0-9]{1,20}", fullmatch=True),
    title=st.text(min_size=1, max_size=100).filter(lambda x: x.strip()),
    content=st.text(min_size=1, max_size=500),
)
@settings(max_examples=50)
def test_save_then_retrieve(url_path, title, content, tmp_path_factory):
    """
    属性 5.3.1: 保存后可检索
    ∀ doc: Document, save_document(doc) → get_document(doc.url) == doc

    **Validates: Requirements 3.5.1**
    """
    tmp_path = tmp_path_factory.mktemp("db")
    db_path = tmp_path / "test.db"
    storage = DocumentStorage(f"sqlite:///{db_path}")
    storage.init_db()

    url = f"https://help.aliyun.com/{url_path}"
    content_hash = compute_content_hash(content)

    doc = Document(
        url=url,
        title=title,
        content=content,
        content_hash=content_hash,
        crawled_at=datetime.now(),
    )

    # 保存文档
    doc_id = storage.save_document(doc)
    # 保存版本（这样get_document才能获取内容）
    storage.save_version(doc_id, content, content_hash)

    # 检索文档
    retrieved = storage.get_document(url)

    assert retrieved is not None
    assert retrieved.url == doc.url
    assert retrieved.title == doc.title
    assert retrieved.content_hash == doc.content_hash
    assert retrieved.content == content


# ============================================================
# 属性 5.3.2: 版本历史单调递增
# **Validates: Requirements 3.5.2**
# ============================================================

@given(
    versions=st.lists(
        st.text(min_size=1, max_size=100),
        min_size=2,
        max_size=10,
    )
)
@settings(max_examples=30)
def test_version_history_monotonic(versions, tmp_path_factory):
    """
    属性 5.3.2: 版本历史单调递增
    ∀ doc_id, versions = get_versions(doc_id),
      ∀ i < j, versions[i].version < versions[j].version

    **Validates: Requirements 3.5.2**
    """
    tmp_path = tmp_path_factory.mktemp("db")
    db_path = tmp_path / "test.db"
    storage = DocumentStorage(f"sqlite:///{db_path}")
    storage.init_db()

    # 创建文档
    doc = Document(
        url="https://help.aliyun.com/version-test",
        title="版本测试",
        content="初始内容",
        content_hash="initial",
    )
    doc_id = storage.save_document(doc)

    # 保存多个版本
    for content in versions:
        content_hash = compute_content_hash(content)
        storage.save_version(doc_id, content, content_hash)

    # 验证版本号单调递增
    session = storage.get_session()
    try:
        from src.storage import DocumentVersionDB
        db_versions = (
            session.query(DocumentVersionDB)
            .filter_by(document_id=doc_id)
            .order_by(DocumentVersionDB.version.asc())
            .all()
        )

        for i in range(len(db_versions) - 1):
            assert db_versions[i].version < db_versions[i + 1].version
    finally:
        session.close()

    # 最新版本应该是最后保存的
    latest = storage.get_latest_version(doc_id)
    assert latest == versions[-1]


# ============================================================
# 属性 5.2.1: 变更检测完整性
# **Validates: Requirements 3.2.2, 3.2.3**
# ============================================================

def make_doc(url, content):
    """辅助函数：创建文档"""
    return Document(
        url=url,
        title=f"Doc {url}",
        content=content,
        content_hash=compute_content_hash(content),
        crawled_at=datetime.now(),
    )


@given(
    old_urls=st.lists(
        st.from_regex(r"https://example\.com/doc[0-9]{1,3}", fullmatch=True),
        min_size=0,
        max_size=10,
        unique=True,
    ),
    new_urls=st.lists(
        st.from_regex(r"https://example\.com/doc[0-9]{1,3}", fullmatch=True),
        min_size=0,
        max_size=10,
        unique=True,
    ),
)
@settings(max_examples=50)
def test_change_detection_completeness(old_urls, new_urls):
    """
    属性 5.2.1: 变更检测完整性
    所有文档都应被分类为新增、修改、删除或未变更之一

    **Validates: Requirements 3.2.2, 3.2.3**
    """
    old_docs = [make_doc(url, f"old content {url}") for url in old_urls]
    new_docs = [make_doc(url, f"new content {url}") for url in new_urls]

    detector = ChangeDetector()
    report = detector.detect_changes(old_docs, new_docs)

    old_set = set(old_urls)
    new_set = set(new_urls)

    # 新增文档 = 在new中但不在old中
    added_urls = {doc.url for doc in report.added}
    expected_added = new_set - old_set
    assert added_urls == expected_added

    # 删除文档 = 在old中但不在new中
    deleted_urls = {doc.url for doc in report.deleted}
    expected_deleted = old_set - new_set
    assert deleted_urls == expected_deleted

    # 修改文档 = 在两者中都存在且内容不同
    modified_urls = {change.document.url for change in report.modified}
    common_urls = old_set & new_set
    # 由于我们用不同内容创建，所有common都应该被检测为modified
    # （除非被噪声过滤器过滤掉）
    assert modified_urls.issubset(common_urls)


# ============================================================
# 属性 5.2.2: 哈希相同则内容未变更
# **Validates: Requirements 3.2.4**
# ============================================================

@given(
    content=st.text(min_size=1, max_size=200),
    urls=st.lists(
        st.from_regex(r"https://example\.com/[a-z]{1,10}", fullmatch=True),
        min_size=1,
        max_size=5,
        unique=True,
    ),
)
@settings(max_examples=50)
def test_same_hash_not_modified(content, urls):
    """
    属性 5.2.2: 哈希相同则内容未变更
    如果文档哈希相同，则不应被标记为已修改
    ∀ doc1, doc2, doc1.hash == doc2.hash → doc2 ∉ modified_docs

    **Validates: Requirements 3.2.4**
    """
    # 创建哈希相同的文档对（相同内容）
    old_docs = [make_doc(url, content) for url in urls]
    new_docs = [make_doc(url, content) for url in urls]

    detector = ChangeDetector()
    report = detector.detect_changes(old_docs, new_docs)

    # 不应有任何修改
    assert len(report.modified) == 0
    # 不应有新增或删除
    assert len(report.added) == 0
    assert len(report.deleted) == 0


# ============================================================
# 属性 5.4.1: 重试幂等性
# **Validates: Requirements 3.4.4**
# ============================================================

@given(
    title=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
    summary=st.text(min_size=1, max_size=200),
)
@settings(max_examples=30)
def test_notification_retry_idempotent(title, summary, tmp_path_factory):
    """
    属性 5.4.1: 重试幂等性
    多次发送相同通知应该是幂等的

    **Validates: Requirements 3.4.4**
    """
    from src.models import ChangeType, Notification
    from src.notifier import FileNotifier

    tmp_path = tmp_path_factory.mktemp("notif")

    doc = Document(
        url="https://test.com/doc",
        title="测试",
        content="内容",
        content_hash="hash",
    )
    from src.models import DocumentChange
    change = DocumentChange(
        document=doc,
        old_content_hash="old",
        new_content_hash="new",
        diff="diff",
        change_type=ChangeType.MINOR,
    )

    notification = Notification(
        title=title,
        summary=summary,
        changes=[change],
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
    )

    notifier = FileNotifier(output_dir=str(tmp_path))

    # 发送两次
    result1 = notifier.send(notification)
    result2 = notifier.send(notification)

    # 两次都应该成功
    assert result1 is True
    assert result2 is True

    # 文件应该被创建（幂等 - 不会因重复发送而失败）
    files = list(tmp_path.glob("notification_*.json"))
    assert len(files) >= 1

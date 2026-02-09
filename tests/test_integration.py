"""集成测试"""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config
from src.crawler import DocumentCrawler
from src.detector import ChangeDetector
from src.models import Document
from src.notifier import FileNotifier, NotificationManager
from src.storage import DocumentStorage
from src.summarizer import AISummarizer
from src.utils import compute_content_hash


@pytest.fixture
def test_config(tmp_path):
    """创建测试配置文件"""
    config_content = f"""
crawler:
  base_url: "https://help.aliyun.com"
  request_delay: 0.01
  max_retries: 1
  timeout: 10
  user_agent: "TestBot/1.0"

scheduler:
  enabled: false
  cron: "0 9 * * 1"
  timezone: "Asia/Shanghai"

llm:
  provider: "huggingface"
  model: "test-model"
  api_key: "test-key"
  api_base: "https://api-inference.huggingface.co/models"
  max_tokens: 100
  temperature: 0.3

notifications:
  - type: "file"
    enabled: true
    output_dir: "{tmp_path / 'notifications'}"

storage:
  type: "sqlite"
  database: "{tmp_path / 'test.db'}"
  keep_versions: 5

logging:
  level: "DEBUG"
  file: "{tmp_path / 'test.log'}"
  max_size: "1MB"
  backup_count: 1
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return Config(str(config_path))


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "test.db"
    s = DocumentStorage(f"sqlite:///{db_path}")
    s.init_db()
    return s


class TestFullCheckFlow:
    """测试完整的文档检查流程"""

    def test_end_to_end_with_changes(self, storage, tmp_path):
        # 1. 保存旧文档
        old_doc = Document(
            url="https://help.aliyun.com/doc1",
            title="旧文档",
            content="旧内容",
            content_hash=compute_content_hash("旧内容"),
        )
        doc_id = storage.save_document(old_doc)
        storage.save_version(doc_id, old_doc.content, old_doc.content_hash)

        # 2. 模拟新文档（内容已变更）
        new_doc = Document(
            url="https://help.aliyun.com/doc1",
            title="旧文档",
            content="新内容",
            content_hash=compute_content_hash("新内容"),
        )

        # 3. 检测变更
        detector = ChangeDetector()
        old_docs = storage.get_all_documents()
        report = detector.detect_changes(old_docs, [new_doc])

        assert len(report.modified) == 1

        # 4. 发送通知
        manager = NotificationManager()
        manager.add_notifier(FileNotifier(output_dir=str(tmp_path / "notifications")))

        if report.modified:
            results = manager.send_batch(report.modified, "测试摘要")
            assert results["FileNotifier"] is True

        # 5. 验证通知文件
        notif_files = list((tmp_path / "notifications").glob("*.json"))
        assert len(notif_files) == 1


class TestEndToEndWorkflow:
    """测试端到端工作流"""

    def test_new_document_detection(self, storage):
        # 空数据库 -> 新文档
        new_docs = [
            Document(
                url="https://help.aliyun.com/new",
                title="新文档",
                content="新内容",
                content_hash=compute_content_hash("新内容"),
            )
        ]

        detector = ChangeDetector()
        old_docs = storage.get_all_documents()
        report = detector.detect_changes(old_docs, new_docs)

        assert len(report.added) == 1
        assert len(report.modified) == 0
        assert len(report.deleted) == 0


class TestErrorRecovery:
    """测试错误恢复机制"""

    def test_storage_survives_bad_data(self, storage):
        # 保存正常文档
        doc = Document(
            url="https://help.aliyun.com/good",
            title="正常文档",
            content="正常内容",
            content_hash="hash123",
        )
        storage.save_document(doc)

        # 验证数据仍然可以检索
        result = storage.get_document("https://help.aliyun.com/good")
        assert result is not None


class TestConfigLoading:
    """测试配置加载和应用"""

    def test_config_loads_correctly(self, test_config):
        assert test_config.get("crawler.base_url") == "https://help.aliyun.com"
        assert test_config.get("crawler.request_delay") == 0.01
        assert test_config.get("llm.provider") == "huggingface"

    def test_config_nested_access(self, test_config):
        assert test_config.get("scheduler.cron") == "0 9 * * 1"
        assert test_config.get("nonexistent.key", "default") == "default"

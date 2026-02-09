"""通知模块单元测试"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.models import ChangeType, Document, DocumentChange, Notification
from src.notifier import FileNotifier, NotificationManager, WebhookNotifier


@pytest.fixture
def sample_notification():
    doc = Document(
        url="https://help.aliyun.com/test",
        title="测试文档",
        content="内容",
        content_hash="hash123",
    )
    change = DocumentChange(
        document=doc,
        old_content_hash="old",
        new_content_hash="new",
        diff="-旧\n+新",
        change_type=ChangeType.MINOR,
    )
    return Notification(
        title="测试通知",
        summary="这是测试摘要",
        changes=[change],
        timestamp=datetime(2026, 2, 6, 10, 0, 0),
    )


class TestWebhookNotifier:
    """测试WebhookNotifier发送功能"""

    def test_send_success(self, sample_notification):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("src.notifier.requests.post", return_value=mock_response):
            notifier = WebhookNotifier(url="https://webhook.test/hook")
            result = notifier.send(sample_notification)
            assert result is True

    def test_send_failure_with_retry(self, sample_notification):
        with patch("src.notifier.requests.post", side_effect=Exception("连接失败")):
            with patch("src.notifier.time.sleep"):
                notifier = WebhookNotifier(
                    url="https://webhook.test/hook", retry_count=2
                )
                result = notifier.send(sample_notification)
                assert result is False


class TestFileNotifier:
    """测试FileNotifier输出功能"""

    def test_send_creates_file(self, tmp_path, sample_notification):
        notifier = FileNotifier(output_dir=str(tmp_path))
        result = notifier.send(sample_notification)
        assert result is True

        # 检查文件是否创建
        files = list(tmp_path.glob("notification_*.json"))
        assert len(files) == 1

        # 检查文件内容
        with open(files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["title"] == "测试通知"
        assert data["total_changes"] == 1


class TestRetryMechanism:
    """测试重试机制"""

    def test_retry_with_eventual_success(self, sample_notification):
        mock_fail = MagicMock(side_effect=Exception("失败"))
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.raise_for_status = MagicMock()

        with patch("src.notifier.requests.post", side_effect=[Exception("失败"), mock_ok]):
            with patch("src.notifier.time.sleep"):
                notifier = WebhookNotifier(
                    url="https://webhook.test/hook", retry_count=3
                )
                result = notifier.send(sample_notification)
                assert result is True


class TestNotificationFormatting:
    """测试通知格式化"""

    def test_webhook_payload_format(self, sample_notification):
        notifier = WebhookNotifier(url="https://test.com")
        payload = notifier._format_payload(sample_notification)

        assert "title" in payload
        assert "timestamp" in payload
        assert "summary" in payload
        assert "changes" in payload
        assert len(payload["changes"]) == 1
        assert payload["changes"][0]["change_type"] == "minor"

    def test_file_output_format(self, sample_notification):
        notifier = FileNotifier()
        output = notifier._format_output(sample_notification)

        assert output["total_changes"] == 1
        assert "changes" in output


class TestNotificationManager:
    """测试通知管理器"""

    def test_send_all_channels(self, tmp_path, sample_notification):
        manager = NotificationManager()
        manager.add_notifier(FileNotifier(output_dir=str(tmp_path)))

        results = manager.send_all(sample_notification)
        assert "FileNotifier" in results
        assert results["FileNotifier"] is True

    def test_history_tracking(self, tmp_path, sample_notification):
        manager = NotificationManager()
        manager.add_notifier(FileNotifier(output_dir=str(tmp_path)))

        manager.send_all(sample_notification)
        history = manager.get_history()
        assert len(history) == 1
        assert history[0]["success"] is True

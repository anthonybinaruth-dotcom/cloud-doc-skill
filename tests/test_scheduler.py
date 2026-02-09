"""调度器模块单元测试"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler import DocumentMonitorScheduler


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "scheduler.cron": "0 9 * * 1",
        "scheduler.timezone": "Asia/Shanghai",
        "crawler.base_url": "https://help.aliyun.com",
    }.get(key, default)
    return config


@pytest.fixture
def scheduler(mock_config):
    storage = MagicMock()
    storage.get_all_documents.return_value = []
    storage.save_scan_record.return_value = 1

    crawler = MagicMock()
    crawler.crawl_site.return_value = []

    detector = MagicMock()
    summarizer = MagicMock()
    notification_manager = MagicMock()

    return DocumentMonitorScheduler(
        config=mock_config,
        storage=storage,
        crawler=crawler,
        detector=detector,
        summarizer=summarizer,
        notification_manager=notification_manager,
    )


class TestSchedulerStart:
    """测试定时任务触发"""

    def test_start_scheduler(self, scheduler):
        scheduler.start()
        assert scheduler.is_running is True
        scheduler.stop()

    def test_start_already_running(self, scheduler):
        scheduler.start()
        scheduler.start()  # 不应报错
        assert scheduler.is_running is True
        scheduler.stop()

    def test_stop_scheduler(self, scheduler):
        scheduler.start()
        scheduler.stop()
        assert scheduler.is_running is False


class TestManualTrigger:
    """测试手动触发功能"""

    def test_run_check_now(self, scheduler):
        from src.models import ChangeReport
        scheduler.detector.detect_changes.return_value = ChangeReport()

        scheduler.run_check_now()

        # 验证各模块被调用
        scheduler.storage.save_scan_record.assert_called_once()
        scheduler.crawler.crawl_site.assert_called_once()
        scheduler.detector.detect_changes.assert_called_once()


class TestTaskExecution:
    """测试任务执行流程"""

    def test_check_updates_scan_record(self, scheduler):
        from src.models import ChangeReport
        scheduler.detector.detect_changes.return_value = ChangeReport()

        scheduler.run_check_now()

        scheduler.storage.update_scan_record.assert_called_once()
        call_kwargs = scheduler.storage.update_scan_record.call_args
        assert call_kwargs[1]["status"] == "completed"


class TestErrorHandling:
    """测试错误处理"""

    def test_check_handles_crawler_error(self, scheduler):
        scheduler.crawler.crawl_site.side_effect = Exception("网络错误")

        # 不应抛出异常
        scheduler.run_check_now()

        # 应该记录失败状态
        call_kwargs = scheduler.storage.update_scan_record.call_args
        assert call_kwargs[1]["status"] == "failed"

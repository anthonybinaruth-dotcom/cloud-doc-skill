"""通知发送模块"""

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .models import DocumentChange, Notification


class Notifier(ABC):
    """通知发送抽象基类"""

    @abstractmethod
    def send(self, notification: Notification) -> bool:
        """
        发送通知

        Args:
            notification: 通知对象

        Returns:
            是否发送成功
        """
        pass


class WebhookNotifier(Notifier):
    """Webhook通知"""

    def __init__(self, url: str, retry_count: int = 3, timeout: int = 30):
        self.url = url
        self.retry_count = retry_count
        self.timeout = timeout

    def send(self, notification: Notification) -> bool:
        payload = self._format_payload(notification)
        last_error = None

        for attempt in range(1, self.retry_count + 1):
            try:
                response = requests.post(
                    self.url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                logging.info(f"Webhook通知发送成功: {self.url}")
                return True
            except Exception as e:
                last_error = e
                if attempt < self.retry_count:
                    wait_time = 2 ** attempt  # 指数退避
                    logging.warning(
                        f"Webhook发送失败 (尝试 {attempt}/{self.retry_count}): {e}. "
                        f"将在 {wait_time} 秒后重试..."
                    )
                    time.sleep(wait_time)

        logging.error(f"Webhook通知发送失败: {last_error}")
        return False

    def _format_payload(self, notification: Notification) -> Dict[str, Any]:
        changes_data = []
        for change in notification.changes:
            changes_data.append({
                "document_title": change.document.title,
                "document_url": change.document.url,
                "change_type": change.change_type.value,
                "detected_at": datetime.now().isoformat(),
            })

        return {
            "title": notification.title,
            "timestamp": notification.timestamp.isoformat(),
            "summary": notification.summary,
            "changes": changes_data,
            "metadata": notification.metadata,
        }


class FileNotifier(Notifier):
    """文件输出通知"""

    def __init__(self, output_dir: str = "./notifications"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def send(self, notification: Notification) -> bool:
        try:
            timestamp = notification.timestamp.strftime("%Y%m%d_%H%M%S")
            filename = f"notification_{timestamp}.json"
            filepath = self.output_dir / filename

            payload = self._format_output(notification)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

            logging.info(f"通知已保存到文件: {filepath}")
            return True
        except Exception as e:
            logging.error(f"文件通知保存失败: {e}")
            return False

    def _format_output(self, notification: Notification) -> Dict[str, Any]:
        changes_data = []
        for change in notification.changes:
            changes_data.append({
                "document_title": change.document.title,
                "document_url": change.document.url,
                "change_type": change.change_type.value,
                "diff_preview": change.diff[:500] if change.diff else "",
            })

        return {
            "title": notification.title,
            "timestamp": notification.timestamp.isoformat(),
            "summary": notification.summary,
            "total_changes": len(notification.changes),
            "changes": changes_data,
        }


class NotificationManager:
    """通知管理器 - 管理多个通知渠道"""

    def __init__(self):
        self.notifiers: List[Notifier] = []
        self._history: List[Dict[str, Any]] = []

    def add_notifier(self, notifier: Notifier) -> None:
        self.notifiers.append(notifier)

    def send_all(self, notification: Notification) -> Dict[str, bool]:
        """
        通过所有渠道发送通知

        Returns:
            各渠道发送结果
        """
        results = {}
        for notifier in self.notifiers:
            channel_name = notifier.__class__.__name__
            success = notifier.send(notification)
            results[channel_name] = success

            self._history.append({
                "channel": channel_name,
                "success": success,
                "timestamp": datetime.now().isoformat(),
                "title": notification.title,
            })

        return results

    def send_batch(self, changes: List[DocumentChange], summary: str) -> Dict[str, bool]:
        """
        发送批量通知

        Args:
            changes: 变更列表
            summary: 总体摘要

        Returns:
            各渠道发送结果
        """
        notification = Notification(
            title=f"阿里云文档更新通知 - 检测到 {len(changes)} 个变更",
            summary=summary,
            changes=changes,
            timestamp=datetime.now(),
        )
        return self.send_all(notification)

    def get_history(self) -> List[Dict[str, Any]]:
        return self._history.copy()

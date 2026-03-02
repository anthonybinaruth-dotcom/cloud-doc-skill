"""通知发送模块"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .models import ChangeReport, DocumentChange, Notification


class NotifierBase(ABC):
    """通知器抽象基类"""

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


class WebhookNotifier(NotifierBase):
    """Webhook 通知器（通用）"""

    def __init__(
        self,
        url: str,
        retry_count: int = 3,
        timeout: int = 30,
    ):
        self.url = url
        self.retry_count = retry_count
        self.timeout = timeout

    def send(self, notification: Notification) -> bool:
        if not self.url:
            logging.warning("Webhook URL 未配置，跳过发送")
            return False

        payload = {
            "title": notification.title,
            "summary": notification.summary,
            "timestamp": notification.timestamp.isoformat(),
            "changes_count": len(notification.changes),
            "metadata": notification.metadata,
        }

        for attempt in range(1, self.retry_count + 1):
            try:
                response = requests.post(
                    self.url,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                logging.info(f"Webhook 通知发送成功: {self.url}")
                return True
            except Exception as e:
                logging.error(f"Webhook 发送失败 (尝试 {attempt}/{self.retry_count}): {e}")
                if attempt < self.retry_count:
                    import time
                    time.sleep(2 * attempt)

        return False

class AiflowNotifier(NotifierBase):
    """aiflow Webhook 通知器（百度内部工作流平台）"""

    def __init__(
        self,
        webhook_url: str,
        retry_count: int = 3,
        timeout: int = 30,
    ):
        self.webhook_url = webhook_url
        self.retry_count = retry_count
        self.timeout = timeout

    def send(self, notification: Notification) -> bool:
        if not self.webhook_url:
            logging.warning("aiflow Webhook URL 未配置，跳过发送")
            return False

        # aiflow 直接发送结构化 JSON，便于后续工作流处理
        payload = {
            "event": "doc_change_notification",
            "user": "alimujiangayiziba",
            "title": notification.title,
            "summary": notification.summary,
            "timestamp": notification.timestamp.isoformat(),
            "metadata": notification.metadata,
            "changes": [
                {
                    "doc_title": change.document.title,
                    "doc_url": change.document.url,
                    "change_type": change.change_type.value,
                }
                for change in notification.changes[:20]
            ],
        }

        for attempt in range(1, self.retry_count + 1):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                logging.info("aiflow Webhook 通知发送成功")
                return True

            except Exception as e:
                logging.error(f"aiflow 发送失败 (尝试 {attempt}/{self.retry_count}): {e}")
                if attempt < self.retry_count:
                    import time
                    time.sleep(2 * attempt)

        return False

    def send_text(self, text: str) -> bool:
        """发送纯文本消息（用于测试）"""
        if not self.webhook_url:
            return False

        payload = {
            "event": "test_message",
            "user": "alimujiangayiziba",
            "text": text,
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            logging.info("aiflow Webhook 文本消息发送成功")
            return True
        except Exception as e:
            logging.error(f"aiflow 文本消息发送失败: {e}")
            return False


class RuliuNotifier(NotifierBase):
    """如流机器人通知器"""

    def __init__(
        self,
        webhook_url: str,
        retry_count: int = 3,
        timeout: int = 30,
    ):
        self.webhook_url = webhook_url
        self.retry_count = retry_count
        self.timeout = timeout

    def _build_markdown_content(self, notification: Notification) -> str:
        """构建 Markdown 格式的消息内容"""
        lines = [
            f"## {notification.title}",
            f"**时间**: {notification.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "### 变更摘要",
            notification.summary,
            "",
        ]

        if notification.changes:
            lines.append("### 变更详情")
            for i, change in enumerate(notification.changes[:10], 1):
                doc = change.document
                change_type_map = {
                    "minor": "🔸 小改动",
                    "major": "?? 大改动",
                    "structural": "🔷 结构变化",
                }
                type_label = change_type_map.get(change.change_type.value, "📝 变更")
                lines.append(f"{i}. {type_label} [{doc.title}]({doc.url})")

            if len(notification.changes) > 10:
                lines.append(f"\n... 还有 {len(notification.changes) - 10} 条变更")

        return "\n".join(lines)

    def send(self, notification: Notification) -> bool:
        if not self.webhook_url:
            logging.warning("如流机器人 Webhook URL 未配置，跳过发送")
            return False

        markdown_content = self._build_markdown_content(notification)

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": notification.title,
                "text": markdown_content,
            }
        }

        for attempt in range(1, self.retry_count + 1):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                result = response.json()
                if result.get("errcode") == 0:
                    logging.info("如流机器人通知发送成功")
                    return True
                else:
                    logging.error(f"如流机器人返回错误: {result}")

            except Exception as e:
                logging.error(f"如流机器人发送失败 (尝试 {attempt}/{self.retry_count}): {e}")
                if attempt < self.retry_count:
                    import time
                    time.sleep(2 * attempt)

        return False

    def send_text(self, text: str) -> bool:
        """发送纯文本消息"""
        if not self.webhook_url:
            return False

        payload = {
            "msgtype": "text",
            "text": {"content": text},
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json().get("errcode") == 0
        except Exception as e:
            logging.error(f"如流机器人文本消息发送失败: {e}")
            return False


class FileNotifier(NotifierBase):
    """文件输出通知器"""

    def __init__(self, output_dir: str = "./notifications"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def send(self, notification: Notification) -> bool:
        try:
            timestamp = notification.timestamp.strftime("%Y%m%d_%H%M%S")
            filename = f"notification_{timestamp}.md"
            filepath = self.output_dir / filename

            content = self._format_notification(notification)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            logging.info(f"通知已保存到文件: {filepath}")
            return True

        except Exception as e:
            logging.error(f"文件通知保存失败: {e}")
            return False

    def _format_notification(self, notification: Notification) -> str:
        """格式化通知内容为 Markdown"""
        lines = [
            f"# {notification.title}",
            f"\n**生成时间**: {notification.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"\n## 摘要\n\n{notification.summary}",
            "\n## 变更详情\n",
        ]

        if notification.changes:
            for i, change in enumerate(notification.changes, 1):
                doc = change.document
                lines.append(f"### {i}. {doc.title}")
                lines.append(f"\n- **URL**: {doc.url}")
                lines.append(f"- **变更类型**: {change.change_type.value}")
                lines.append(f"\n**Diff 摘要**:\n```diff\n{change.diff[:1000]}\n```\n")
        else:
            lines.append("无详细变更记录")

        return "\n".join(lines)


class NotificationManager:
    """通知管理器 - 统一管理多个通知渠道"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化通知管理器

        Args:
            config: 通知配置，包含 notifications 列表
        """
        self.notifiers: List[NotifierBase] = []
        self._init_notifiers(config)

    def _init_notifiers(self, config: Dict[str, Any]) -> None:
        """根据配置初始化通知器"""
        notifications_config = config.get("notifications", [])

        for notifier_config in notifications_config:
            if not notifier_config.get("enabled", True):
                continue

            notifier_type = notifier_config.get("type", "")

            if notifier_type == "webhook":
                self.notifiers.append(WebhookNotifier(
                    url=notifier_config.get("url", ""),
                    retry_count=notifier_config.get("retry_count", 3),
                ))
            elif notifier_type == "aiflow":
                self.notifiers.append(AiflowNotifier(
                    webhook_url=notifier_config.get("webhook_url", ""),
                    retry_count=notifier_config.get("retry_count", 3),
                ))
            elif notifier_type == "ruliu":
                self.notifiers.append(RuliuNotifier(
                    webhook_url=notifier_config.get("webhook_url", ""),
                    retry_count=notifier_config.get("retry_count", 3),
                ))
            elif notifier_type == "file":
                self.notifiers.append(FileNotifier(
                    output_dir=notifier_config.get("output_dir", "./notifications"),
                ))
            else:
                logging.warning(f"未知的通知类型: {notifier_type}")

    def send_all(self, notification: Notification) -> Dict[str, bool]:
        """
        向所有配置的渠道发送通知

        Args:
            notification: 通知对象

        Returns:
            各渠道发送结果
        """
        results = {}
        for notifier in self.notifiers:
            notifier_name = notifier.__class__.__name__
            try:
                results[notifier_name] = notifier.send(notification)
            except Exception as e:
                logging.error(f"{notifier_name} 发送异常: {e}")
                results[notifier_name] = False

        return results

    def notify_changes(self, report: ChangeReport, summary: str) -> Dict[str, bool]:
        """
        发送变更通知的便捷方法

        Args:
            report: 变更报告
            summary: AI 生成的摘要

        Returns:
            各渠道发送结果
        """
        total_changes = len(report.added) + len(report.modified) + len(report.deleted)

        if total_changes == 0:
            logging.info("无变更，跳过通知发送")
            return {}

        notification = Notification(
            title=f"云文档监控报告 - 检测到 {total_changes} 个变更",
            summary=summary,
            changes=report.modified,
            timestamp=report.timestamp,
            metadata={
                "added_count": len(report.added),
                "modified_count": len(report.modified),
                "deleted_count": len(report.deleted),
            }
        )

        return self.send_all(notification)
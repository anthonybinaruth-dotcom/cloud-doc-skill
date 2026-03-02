#!/usr/bin/env python3
"""测试 aiflow Webhook 通知是否配置成功"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.notifier import AiflowNotifier


def test_aiflow():
    """发送测试消息到 aiflow Webhook"""
    
    # 从环境变量获取 webhook URL
    webhook_url = os.environ.get("AIFLOW_WEBHOOK_URL", "")
    
    if not webhook_url:
        print("❌ 错误：未设置 AIFLOW_WEBHOOK_URL 环境变量")
        print()
        print("请先设置环境变量：")
        print("  export AIFLOW_WEBHOOK_URL='你的aiflow Webhook地址'")
        return False
    
    print(f"📡 Webhook URL: {webhook_url[:50]}...")
    print("📌 aiflow 测试模式需先点击「监听测试事件」按钮")
    print()
    
    # 创建通知器
    notifier = AiflowNotifier(webhook_url=webhook_url)
    
    # 发送测试文本消息
    print("正在发送测试消息...")
    success = notifier.send_text(
        "🎉 aiflow Webhook 配置测试成功！\n\n"
        "这是来自「云文档监控助手」的测试消息。"
    )
    
    if success:
        print("✅ 测试成功！请在 aiflow 工作流中查看收到的数据")
        return True
    else:
        print("❌ 测试失败！请检查：")
        print("  1. 是否已点击 aiflow 的「监听测试事件」按钮")
        print("  2. 测试模式每次只能接收一次请求")
        print("  3. 网络是否正常")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("aiflow Webhook 配置测试")
    print("=" * 50)
    print()
    
    success = test_aiflow()
    sys.exit(0 if success else 1)
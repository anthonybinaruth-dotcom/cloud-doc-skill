#!/usr/bin/env python3
"""对比腾讯云和百度云的弹性网卡文档"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tencent_crawler import TencentDocCrawler
from src.baidu_crawler import BaiduDocCrawler

tc = TencentDocCrawler()
bc = BaiduDocCrawler()

print("=" * 60)
print("腾讯云 vs 百度云 弹性网卡文档对比")
print("=" * 60)

# 获取腾讯云弹性网卡主文档
print("\n【腾讯云弹性网卡】")
tencent_doc = tc.fetch_doc(doc_id="113683", product_id="213")
if tencent_doc:
    print(f"标题: {tencent_doc['title']}")
    print(f"URL: {tencent_doc['url']}")
    print(f"内容摘要:\n{tencent_doc['text'][:1000]}")
else:
    print("获取失败")

# 获取百度云弹性网卡主文档
print("\n" + "=" * 60)
print("\n【百度云弹性网卡】")
baidu_doc = bc.fetch_doc("VPC", "0jwvytzll")
if baidu_doc:
    if hasattr(baidu_doc, 'title'):
        print(f"标题: {baidu_doc.title}")
        print(f"URL: {baidu_doc.url}")
        print(f"内容摘要:\n{baidu_doc.content[:1000]}")
    else:
        print(f"标题: {baidu_doc.get('title', 'N/A')}")
        print(f"URL: {baidu_doc.get('url', 'N/A')}")
        content = baidu_doc.get('content', baidu_doc.get('text', ''))
        print(f"内容摘要:\n{content[:1000]}")
else:
    print("获取失败")
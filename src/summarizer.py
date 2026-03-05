"""AI摘要生成模块

支持多模态模型，可以理解文档中的图片内容。
"""

import base64
import hashlib
import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

import requests

from .models import DocumentChange


class LLMAdapter(ABC):
    """大模型适配器抽象基类"""

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        pass


class DashScopeAdapter(LLMAdapter):
    """阿里云通义千问 DashScope API 适配器"""

    def __init__(
        self,
        model: str = "qwen-turbo",
        api_key: str = "",
        api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_retries: int = 3,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_retries = max_retries

    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个技术文档分析助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=60
                )

                if response.status_code == 429:
                    logging.warning(f"API限流，等待重试 (尝试 {attempt}/{self.max_retries})")
                    import time
                    time.sleep(5)
                    continue

                response.raise_for_status()
                result = response.json()
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return str(result)

            except Exception as e:
                last_error = e
                logging.error(f"DashScope API调用失败 (尝试 {attempt}/{self.max_retries}): {e}")

        raise RuntimeError(f"DashScope API调用失败: {last_error}")


class DashScopeVLAdapter(LLMAdapter):
    """阿里云通义千问多模态 (qwen-vl) 适配器 - 支持图片理解"""

    def __init__(
        self,
        model: str = "qwen-vl-plus",
        api_key: str = "",
        api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_retries: int = 3,
        max_images: int = 5,  # 每次请求最多处理的图片数
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_retries = max_retries
        self.max_images = max_images
        self._image_cache: Dict[str, str] = {}  # URL -> base64

    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        """生成文本（不带图片）"""
        return self.generate_with_images(prompt, [], max_tokens)

    def generate_with_images(
        self,
        prompt: str,
        image_urls: List[str],
        max_tokens: int = 1000,
    ) -> str:
        """生成带图片理解的回复
        
        Args:
            prompt: 文本提示
            image_urls: 图片 URL 列表
            max_tokens: 最大输出 token 数
        """
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        # 构建多模态消息内容
        content = []
        
        # 添加图片（限制数量）
        for img_url in image_urls[:self.max_images]:
            img_data = self._get_image_data(img_url)
            if img_data:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img_data}
                })
        
        # 添加文本
        content.append({
            "type": "text",
            "text": prompt
        })
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个技术文档分析助手。请仔细分析文档内容，包括文字和图片。"
                },
                {
                    "role": "user",
                    "content": content
                },
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=120  # 多模态需要更长超时
                )

                if response.status_code == 429:
                    logging.warning(f"API限流，等待重试 (尝试 {attempt}/{self.max_retries})")
                    import time
                    time.sleep(5)
                    continue

                response.raise_for_status()
                result = response.json()
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return str(result)

            except Exception as e:
                last_error = e
                logging.error(f"DashScope VL API调用失败 (尝试 {attempt}/{self.max_retries}): {e}")

        raise RuntimeError(f"DashScope VL API调用失败: {last_error}")

    def _get_image_data(self, url: str) -> Optional[str]:
        """获取图片数据（URL 或 base64）
        
        DashScope VL 支持直接传入图片 URL。
        会先验证 URL 是否可访问，防止截断或无效 URL 导致 API 错误。
        """
        # 检查缓存
        if url in self._image_cache:
            return self._image_cache[url]
        
        try:
            # 先验证 URL 是否可访问（使用 HEAD 请求，快速检查）
            resp = requests.head(url, timeout=5, allow_redirects=True)
            if resp.status_code != 200:
                logging.warning(f"图片 URL 不可访问 (状态码 {resp.status_code}): {url}")
                return None
            
            # 验证通过，缓存并返回
            self._image_cache[url] = url
            return url
                
        except Exception as e:
            logging.warning(f"图片 URL 验证失败 {url}: {e}")
            return None

    def _download_and_encode(self, url: str) -> Optional[str]:
        """下载图片并转为 base64 data URL"""
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0"
            })
            resp.raise_for_status()
            
            # 检测图片类型
            content_type = resp.headers.get("Content-Type", "image/png")
            if "jpeg" in content_type or "jpg" in content_type:
                mime = "image/jpeg"
            elif "gif" in content_type:
                mime = "image/gif"
            elif "webp" in content_type:
                mime = "image/webp"
            else:
                mime = "image/png"
            
            # 转 base64
            b64 = base64.b64encode(resp.content).decode("utf-8")
            data_url = f"data:{mime};base64,{b64}"
            
            self._image_cache[url] = data_url
            return data_url
            
        except Exception as e:
            logging.warning(f"下载图片失败 {url}: {e}")
            return None


def extract_image_urls(content: str) -> List[str]:
    """从 Markdown 内容中提取图片 URL"""
    patterns = [
        r'!\[.*?\]\((https?://[^\)]+)\)',  # ![alt](url)
        r'<img[^>]+src=["\']([^"\']+)["\']',  # <img src="url">
        r'<span>\s*!\[.*?\]\((https?://[^\)]+)\)',  # <span>![](url)
    ]
    
    urls = []
    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        urls.extend(matches)
    
    # 去重并保持顺序
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    
    return unique_urls


# Prompt模板
SYSTEM_PROMPT = "你是一个技术文档分析助手。你的任务是分析文档变更内容，生成简洁的中文摘要。"

CHANGE_SUMMARY_TEMPLATE = """以下是阿里云文档《{title}》的变更内容：

{diff}

请生成一个200-500字的中文摘要，包含：
1. 变更类型（新增/修改/删除）
2. 主要变更点（3-5条）
3. 可能的影响范围
4. 建议的后续行动（如有）

摘要应该：
- 使用简洁的技术语言
- 突出重要信息
- 避免冗余描述"""

BATCH_SUMMARY_TEMPLATE = """以下是本周阿里云文档的变更汇总：

{changes_summary}

请生成一个总体摘要（300-800字），包含：
1. 本周变更概览
2. 重要变更点
3. 可能的影响范围
4. 建议的后续行动"""


class AISummarizer:
    """AI摘要生成类
    
    支持多模态模型，当 LLM 适配器为 DashScopeVLAdapter 时，
    会自动提取文档中的图片并发送给模型进行理解。
    """

    def __init__(self, config_or_adapter, max_tokens: int = 1000, enable_vision: bool = True):
        """初始化 AI 摘要生成器
        
        Args:
            config_or_adapter: 配置对象或 LLM 适配器实例
            max_tokens: 最大输出 token 数
            enable_vision: 是否启用图片理解
        """
        # 支持传入配置对象或直接传入适配器
        if isinstance(config_or_adapter, LLMAdapter):
            self.llm = config_or_adapter
            self.enable_vision = enable_vision
        else:
            # 从配置创建适配器
            config = config_or_adapter
            llm_config = config.get("llm", {}) if hasattr(config, 'get') else {}
            
            api_key = llm_config.get("api_key", "")
            api_base = llm_config.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            model = llm_config.get("model", "qwen-turbo")
            max_tokens = llm_config.get("max_tokens", 1000)
            enable_vision = llm_config.get("enable_vision", False)
            vision_model = llm_config.get("vision_model", "qwen-vl-plus")
            max_images = llm_config.get("max_images", 5)
            
            # 根据配置选择适配器
            if enable_vision:
                logging.info(f"启用多模态模型: {vision_model}")
                self.llm = DashScopeVLAdapter(
                    model=vision_model,
                    api_key=api_key,
                    api_base=api_base,
                    max_images=max_images,
                )
            else:
                self.llm = DashScopeAdapter(
                    model=model,
                    api_key=api_key,
                    api_base=api_base,
                )
            self.enable_vision = enable_vision
        
        self.max_tokens = max_tokens
        self._cache: Dict[str, str] = {}
        # 检查是否支持多模态
        self._is_multimodal = isinstance(self.llm, DashScopeVLAdapter)

    def summarize_change(self, change: DocumentChange) -> str:
        """为单个文档变更生成摘要"""
        cache_key = self._get_cache_key(change.diff)
        if cache_key in self._cache:
            return self._cache[cache_key]

        truncated_diff = self._truncate_content(change.diff, max_chars=3000)
        prompt = f"{SYSTEM_PROMPT}\n\n{CHANGE_SUMMARY_TEMPLATE.format(title=change.document.title, diff=truncated_diff)}"

        try:
            # 如果支持多模态且启用，提取并发送图片
            if self._is_multimodal and self.enable_vision:
                image_urls = extract_image_urls(change.diff)
                if image_urls:
                    logging.info(f"文档《{change.document.title}》包含 {len(image_urls)} 张图片，使用多模态分析")
                    summary = self.llm.generate_with_images(prompt, image_urls, self.max_tokens)
                else:
                    summary = self.llm.generate(prompt, self.max_tokens)
            else:
                summary = self.llm.generate(prompt, self.max_tokens)
            
            self._cache[cache_key] = summary
            return summary
        except Exception as e:
            logging.error(f"生成摘要失败 ({change.document.title}): {e}")
            return f"摘要生成失败: {e}"

    def summarize_content_with_images(self, title: str, content: str) -> str:
        """对包含图片的内容进行多模态分析
        
        Args:
            title: 文档标题
            content: 文档内容（Markdown 格式，可能包含图片）
            
        Returns:
            AI 生成的摘要
        """
        truncated_content = self._truncate_content(content, max_chars=4000)
        prompt = f"""请分析以下云产品文档《{title}》的内容：

{truncated_content}

请生成一个300-600字的中文摘要，包含：
1. 文档主题和核心内容
2. 关键功能点（3-5条）
3. 图片/架构图说明的内容（如有）
4. 适用场景或使用建议

摘要应该：
- 使用简洁的技术语言
- 准确描述图片中的架构或流程
- 突出重要信息"""

        try:
            if self._is_multimodal and self.enable_vision:
                image_urls = extract_image_urls(content)
                if image_urls:
                    logging.info(f"文档《{title}》包含 {len(image_urls)} 张图片，使用多模态分析")
                    return self.llm.generate_with_images(prompt, image_urls, self.max_tokens)
            return self.llm.generate(prompt, self.max_tokens)
        except Exception as e:
            logging.error(f"多模态分析失败 ({title}): {e}")
            return f"分析失败: {e}"

    def summarize_batch(self, changes: List[DocumentChange]) -> str:
        """为批量变更生成总体摘要"""
        if not changes:
            return "本周未检测到文档变更。"

        changes_summary_parts = []
        for i, change in enumerate(changes, 1):
            individual_summary = self.summarize_change(change)
            changes_summary_parts.append(
                f"{i}. 《{change.document.title}》\n"
                f"   变更类型: {change.change_type.value}\n"
                f"   摘要: {individual_summary[:200]}"
            )

        changes_summary = "\n\n".join(changes_summary_parts)
        prompt = f"{SYSTEM_PROMPT}\n\n{BATCH_SUMMARY_TEMPLATE.format(changes_summary=changes_summary)}"

        try:
            return self.llm.generate(prompt, self.max_tokens)
        except Exception as e:
            logging.error(f"生成批量摘要失败: {e}")
            return f"批量摘要生成失败: {e}"

    def _truncate_content(self, content: str, max_chars: int = 3000) -> str:
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n\n... (内容已截断)"

    def _get_cache_key(self, content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()

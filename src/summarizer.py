"""AI摘要生成模块"""

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import requests

from .models import DocumentChange


class LLMAdapter(ABC):
    """大模型适配器抽象基类"""

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        """
        生成文本

        Args:
            prompt: 提示词
            max_tokens: 最大token数

        Returns:
            生成的文本
        """
        pass


class HuggingFaceAdapter(LLMAdapter):
    """HuggingFace Inference API适配器"""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: str = "https://api-inference.huggingface.co/models",
        max_retries: int = 3,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_retries = max_retries
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        url = f"{self.api_base}/{self.model}"
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": max_tokens,
                "temperature": 0.3,
                "return_full_text": False,
            },
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    url, headers=self.headers, json=payload, timeout=60
                )

                if response.status_code == 503:
                    # 模型正在加载
                    logging.warning(f"模型加载中，等待重试 (尝试 {attempt}/{self.max_retries})")
                    import time
                    time.sleep(20)
                    continue

                if response.status_code == 429:
                    # 限流
                    logging.warning(f"API限流，等待重试 (尝试 {attempt}/{self.max_retries})")
                    import time
                    time.sleep(10)
                    continue

                response.raise_for_status()
                result = response.json()

                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("generated_text", "")
                return str(result)

            except Exception as e:
                last_error = e
                logging.error(f"HuggingFace API调用失败 (尝试 {attempt}/{self.max_retries}): {e}")

        raise RuntimeError(f"HuggingFace API调用失败: {last_error}")


class OllamaAdapter(LLMAdapter):
    """Ollama本地模型适配器"""

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        api_base: str = "http://localhost:11434",
    ):
        self.model = model
        self.api_base = api_base

    def generate(self, prompt: str, max_tokens: int = 1000) -> str:
        url = f"{self.api_base}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "options": {"num_predict": max_tokens, "temperature": 0.3},
            "stream": False,
        }

        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except Exception as e:
            raise RuntimeError(f"Ollama API调用失败: {e}")


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
    """AI摘要生成类"""

    def __init__(self, llm_adapter: LLMAdapter, max_tokens: int = 1000):
        self.llm = llm_adapter
        self.max_tokens = max_tokens
        self._cache: Dict[str, str] = {}

    def summarize_change(self, change: DocumentChange) -> str:
        """
        为单个文档变更生成摘要

        Args:
            change: 文档变更对象

        Returns:
            摘要文本
        """
        # 检查缓存
        cache_key = self._get_cache_key(change.diff)
        if cache_key in self._cache:
            logging.debug(f"使用缓存摘要: {change.document.title}")
            return self._cache[cache_key]

        # 截断diff内容（避免超出token限制）
        truncated_diff = self._truncate_content(change.diff, max_chars=3000)

        prompt = f"{SYSTEM_PROMPT}\n\n{CHANGE_SUMMARY_TEMPLATE.format(title=change.document.title, diff=truncated_diff)}"

        try:
            summary = self.llm.generate(prompt, self.max_tokens)
            self._cache[cache_key] = summary
            logging.info(f"已生成摘要: {change.document.title}")
            return summary
        except Exception as e:
            logging.error(f"生成摘要失败 ({change.document.title}): {e}")
            return f"摘要生成失败: {e}"

    def summarize_batch(self, changes: List[DocumentChange]) -> str:
        """
        为批量变更生成总体摘要

        Args:
            changes: 文档变更列表

        Returns:
            总体摘要文本
        """
        if not changes:
            return "本周未检测到文档变更。"

        # 构建变更汇总
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

    def chunk_content(self, content: str, max_tokens: int = 2000) -> List[str]:
        """
        分割长内容

        Args:
            content: 原始内容
            max_tokens: 每段最大token数（近似按字符数估算）

        Returns:
            分段后的内容列表
        """
        # 粗略估算：1个token约等于1.5个中文字符或4个英文字符
        max_chars = max_tokens * 2

        if len(content) <= max_chars:
            return [content]

        chunks = []
        lines = content.split("\n")
        current_chunk = []
        current_length = 0

        for line in lines:
            line_length = len(line) + 1  # +1 for newline
            if current_length + line_length > max_chars and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = line_length
            else:
                current_chunk.append(line)
                current_length += line_length

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks

    def _truncate_content(self, content: str, max_chars: int = 3000) -> str:
        """截断内容"""
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "\n\n... (内容已截断)"

    def _get_cache_key(self, content: str) -> str:
        """生成缓存键"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()

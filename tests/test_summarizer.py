"""AI摘要模块单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from src.models import ChangeType, Document, DocumentChange
from src.summarizer import AISummarizer, HuggingFaceAdapter


@pytest.fixture
def mock_llm():
    """创建mock LLM适配器"""
    adapter = MagicMock()
    adapter.generate.return_value = "这是一个测试摘要，描述了文档的变更内容。"
    return adapter


@pytest.fixture
def summarizer(mock_llm):
    return AISummarizer(mock_llm, max_tokens=500)


@pytest.fixture
def sample_change():
    doc = Document(
        url="https://help.aliyun.com/test",
        title="ECS实例规格",
        content="新内容",
        content_hash="newhash",
    )
    return DocumentChange(
        document=doc,
        old_content_hash="oldhash",
        new_content_hash="newhash",
        diff="-旧内容\n+新内容",
        change_type=ChangeType.MAJOR,
    )


class TestHuggingFaceAdapter:
    """使用mock测试HuggingFaceAdapter"""

    def test_generate_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"generated_text": "测试摘要"}]
        mock_response.raise_for_status = MagicMock()

        with patch("src.summarizer.requests.post", return_value=mock_response):
            adapter = HuggingFaceAdapter(
                model="test-model", api_key="test-key"
            )
            result = adapter.generate("测试提示词")
            assert result == "测试摘要"

    def test_generate_rate_limit(self):
        # 第一次返回429，第二次成功
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.raise_for_status.side_effect = Exception("Rate limited")

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = [{"generated_text": "成功"}]
        mock_ok.raise_for_status = MagicMock()

        import time as time_module
        with patch("src.summarizer.requests.post", side_effect=[mock_429, mock_ok]):
            with patch.object(time_module, "sleep"):
                adapter = HuggingFaceAdapter(
                    model="test-model", api_key="test-key", max_retries=2
                )
                result = adapter.generate("测试")
                assert result == "成功"


class TestChunkContent:
    """测试内容分块功能"""

    def test_short_content_no_chunking(self, summarizer):
        content = "短内容"
        chunks = summarizer.chunk_content(content, max_tokens=1000)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_long_content_chunked(self, summarizer):
        content = "\n".join([f"这是第{i}行内容，包含一些文字。" for i in range(100)])
        chunks = summarizer.chunk_content(content, max_tokens=50)
        assert len(chunks) > 1

    def test_chunk_preserves_content(self, summarizer):
        lines = [f"行{i}" for i in range(20)]
        content = "\n".join(lines)
        chunks = summarizer.chunk_content(content, max_tokens=10)
        # 所有chunk合并后应包含所有原始行
        combined = "\n".join(chunks)
        for line in lines:
            assert line in combined


class TestSummarizeChange:
    """测试摘要生成功能"""

    def test_summarize_single_change(self, summarizer, sample_change):
        result = summarizer.summarize_change(sample_change)
        assert len(result) > 0
        # 验证LLM被调用
        summarizer.llm.generate.assert_called_once()

    def test_summarize_uses_cache(self, summarizer, sample_change):
        # 第一次调用
        summarizer.summarize_change(sample_change)
        # 第二次调用（应该使用缓存）
        summarizer.summarize_change(sample_change)
        # LLM应该只被调用一次
        assert summarizer.llm.generate.call_count == 1

    def test_summarize_batch(self, summarizer, sample_change):
        changes = [sample_change]
        result = summarizer.summarize_batch(changes)
        assert len(result) > 0


class TestErrorHandling:
    """测试错误处理和重试"""

    def test_summarize_handles_llm_error(self):
        mock_llm = MagicMock()
        mock_llm.generate.side_effect = RuntimeError("API错误")

        summarizer = AISummarizer(mock_llm)
        doc = Document(
            url="https://test.com", title="测试",
            content="内容", content_hash="hash",
        )
        change = DocumentChange(
            document=doc, old_content_hash="old",
            new_content_hash="new", diff="diff",
            change_type=ChangeType.MINOR,
        )

        result = summarizer.summarize_change(change)
        assert "失败" in result

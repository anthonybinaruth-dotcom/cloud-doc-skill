"""爬虫模块单元测试"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.crawler import DocumentCrawler


@pytest.fixture
def crawler():
    """创建测试用爬虫实例"""
    with patch.object(DocumentCrawler, '_init_robots_parser'):
        c = DocumentCrawler(
            base_url="https://help.aliyun.com",
            request_delay=0.01,  # 测试时缩短延迟
        )
    return c


class TestParseDocument:
    """测试parse_document功能"""

    def test_parse_basic_html(self, crawler):
        html = """
        <html>
        <head><title>测试文档</title></head>
        <body>
        <article>
            <h1>测试标题</h1>
            <p>这是测试内容。</p>
        </article>
        </body>
        </html>
        """
        doc = crawler.parse_document(html, "https://help.aliyun.com/test")
        assert doc.title == "测试文档"
        assert "测试内容" in doc.content
        assert doc.url == "https://help.aliyun.com/test"
        assert doc.content_hash != ""

    def test_parse_no_title(self, crawler):
        html = """
        <html><body>
        <h1>H1标题</h1>
        <p>内容</p>
        </body></html>
        """
        doc = crawler.parse_document(html, "https://help.aliyun.com/test")
        assert doc.title == "H1标题"

    def test_parse_empty_html(self, crawler):
        html = "<html><body></body></html>"
        doc = crawler.parse_document(html, "https://help.aliyun.com/test")
        assert doc.title == ""
        assert doc.content_hash != ""


class TestExtractLinks:
    """测试extract_links功能"""

    def test_extract_absolute_links(self, crawler):
        html = """
        <html><body>
        <a href="https://help.aliyun.com/doc1">文档1</a>
        <a href="https://help.aliyun.com/doc2">文档2</a>
        </body></html>
        """
        links = crawler.extract_links(html, "https://help.aliyun.com")
        assert len(links) == 2
        assert "https://help.aliyun.com/doc1" in links
        assert "https://help.aliyun.com/doc2" in links

    def test_extract_relative_links(self, crawler):
        html = """
        <html><body>
        <a href="/doc1">文档1</a>
        <a href="/doc2">文档2</a>
        </body></html>
        """
        links = crawler.extract_links(html, "https://help.aliyun.com")
        assert len(links) == 2

    def test_filter_external_links(self, crawler):
        html = """
        <html><body>
        <a href="https://help.aliyun.com/doc1">内部</a>
        <a href="https://www.google.com">外部</a>
        </body></html>
        """
        links = crawler.extract_links(html, "https://help.aliyun.com")
        assert len(links) == 1
        assert "google.com" not in links[0]

    def test_deduplicate_links(self, crawler):
        html = """
        <html><body>
        <a href="/doc1">链接1</a>
        <a href="/doc1">链接1重复</a>
        <a href="/doc2">链接2</a>
        </body></html>
        """
        links = crawler.extract_links(html, "https://help.aliyun.com")
        assert len(links) == 2


class TestURLDedup:
    """测试URL去重功能"""

    def test_visited_urls_tracking(self, crawler):
        assert len(crawler.visited_urls) == 0
        crawler.visited_urls.add("https://help.aliyun.com/doc1")
        assert "https://help.aliyun.com/doc1" in crawler.visited_urls


class TestRateLimit:
    """测试请求频率限制"""

    def test_rate_limit_enforced(self, crawler):
        crawler.request_delay = 0.1
        crawler.last_request_time = time.time()

        start = time.time()
        crawler._rate_limit()
        elapsed = time.time() - start

        # 应该等待了至少部分延迟时间
        assert elapsed >= 0.05


class TestMockNetworkRequests:
    """使用mock测试网络请求"""

    def test_fetch_page_success(self, crawler):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>test</body></html>"
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        with patch.object(crawler.session, 'get', return_value=mock_response):
            html = crawler._fetch_page("https://help.aliyun.com/test")
            assert html == "<html><body>test</body></html>"

    def test_crawl_page_with_mock(self, crawler):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html>
        <head><title>测试页面</title></head>
        <body><article>测试内容</article></body>
        </html>
        """
        mock_response.apparent_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()

        # Mock robots.txt检查，允许爬取
        with patch.object(crawler, '_can_fetch', return_value=True):
            with patch.object(crawler.session, 'get', return_value=mock_response):
                doc = crawler.crawl_page("https://help.aliyun.com/test")
                assert doc.title == "测试页面"
                assert "测试内容" in doc.content

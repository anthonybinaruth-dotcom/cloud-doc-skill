"""MCP 文档获取工具。"""

from __future__ import annotations

import logging
from typing import Dict, List

from fastmcp import Context, FastMCP

from .crawler import DocumentCrawler
from .mcp_services import AppServices
from .tencent_crawler import TencentDocCrawler


def register_doc_tools(mcp: FastMCP, services: AppServices) -> None:
    async def _search_and_get_doc(
        crawler: DocumentCrawler,
        query: str,
        ctx: Context,
    ) -> str:
        """用 AI 理解查询意图，自动识别阿里云产品并选取文档。"""
        summarizer = services.get_summarizer()

        product_prompt = (
            f"用户想查找的阿里云文档：「{query}」\n\n"
            f"请判断这个查询对应阿里云哪个产品，返回该产品的 alias 路径前缀。\n"
            f"常见产品 alias 示例：/ecs、/vpc、/oss、/rds、/slb、/cdn、/ram、/redis、/nas、/ack、/dns、/arms、/fc 等。\n"
            f"只输出一个 alias 路径，如 /ecs，不要输出其他内容。"
        )

        try:
            import re

            await ctx.report_progress(0, 4, "AI 识别产品...")
            product_alias = summarizer.llm.generate(product_prompt, max_tokens=20).strip()
            match = re.search(r"(/[a-z][a-z0-9_-]*)", product_alias.lower())
            product_alias = match.group(1) if match else product_alias.lower().strip()
        except Exception as e:
            return f"AI 识别产品失败: {e}"

        await ctx.report_progress(1, 4, "获取文档目录...")
        aliases = crawler.discover_product_docs(product_alias)
        if not aliases:
            return f"未找到产品 {product_alias} 的文档目录"

        toc_lines = [f"{i}. {alias}" for i, alias in enumerate(aliases)]
        toc_text = "\n".join(toc_lines)
        select_prompt = (
            f"用户想查找的内容：「{query}」\n\n"
            f"以下是该产品的文档目录（编号. alias路径）：\n{toc_text}\n\n"
            f"请从目录中选出与用户查询最相关的1-3篇文档。"
            f"只输出选中文档的编号，用逗号分隔，不要输出其他内容。\n"
            f"例如：5,12,23"
        )

        try:
            import re

            await ctx.report_progress(2, 4, "AI 匹配文档...")
            ai_response = summarizer.llm.generate(select_prompt, max_tokens=50)
            numbers = re.findall(r"\d+", ai_response)
            selected_indices = [int(n) for n in numbers if int(n) < len(aliases)][:3]
        except Exception as e:
            logging.warning(f"AI 选择文档失败: {e}")
            selected_indices = []

        if not selected_indices:
            return f"未找到与 '{query}' 相关的文档"

        await ctx.report_progress(3, 4, "获取文档内容...")
        primary_alias = aliases[selected_indices[0]]
        doc = crawler.crawl_page(primary_alias)
        result = f"标题: {doc.title}\nURL: {doc.url}\n\n{doc.content[:3000]}"

        if len(selected_indices) > 1:
            others = []
            for idx in selected_indices[1:]:
                data = crawler.fetch_doc_by_alias(aliases[idx])
                if data and data.get("title"):
                    others.append(f"  - {data['title']} (alias: {aliases[idx]})")
            if others:
                result += "\n\n--- 其他相关文档 ---\n" + "\n".join(others)

        await ctx.report_progress(4, 4)
        return result

    async def _get_doc_impl(
        url: str,
        ctx: Context,
        cloud: str = "aliyun",
        tencent_product_id: str = "",
        lang: str = "zh",
    ) -> str:
        """获取云文档内容 - 内部实现。"""
        cloud_norm = (cloud or "aliyun").strip().lower()
        if cloud_norm == "tencent":
            try:
                normalized_product_id, normalized_doc_id = services.parse_tencent_doc_ref(
                    url,
                    tencent_product_id,
                )
                if not normalized_doc_id:
                    return f"无法解析腾讯云文档ID: {url}"

                await ctx.report_progress(0, 1, "正在获取腾讯云文档...")
                crawler = TencentDocCrawler(request_delay=0.3)
                doc = crawler.fetch_doc(
                    doc_id=normalized_doc_id,
                    product_id=normalized_product_id,
                    lang=lang,
                )
                if not doc:
                    return (
                        "获取腾讯云文档失败: "
                        f"doc_id={normalized_doc_id}, product_id={normalized_product_id or 'unknown'}"
                    )
                await ctx.report_progress(1, 1)
                return (
                    f"标题: {doc.get('title', '')}\n"
                    f"URL: {doc.get('url', '')}\n\n"
                    f"{doc.get('text', '')[:3000]}"
                )
            except Exception as e:
                return f"获取腾讯云文档失败: {e}"

        if cloud_norm != "aliyun":
            return f"不支持的 cloud: {cloud}，当前仅支持 aliyun 或 tencent"

        try:
            crawler = DocumentCrawler(request_delay=0.5)

            is_url = url.startswith("http")
            is_alias = url.startswith("/") and " " not in url
            if is_url or is_alias:
                try:
                    await ctx.report_progress(0, 1, "正在获取文档...")
                    doc = crawler.crawl_page(url)
                    await ctx.report_progress(1, 1)
                    return f"标题: {doc.title}\nURL: {doc.url}\n\n{doc.content[:3000]}"
                except Exception:
                    pass

            return await _search_and_get_doc(crawler, url, ctx)
        except Exception as e:
            return f"获取失败: {e}"

    @mcp.tool()
    async def get_doc(
        url: str,
        ctx: Context,
        cloud: str = "aliyun",
        tencent_product_id: str = "",
        lang: str = "zh",
    ) -> str:
        """获取云文档内容。默认阿里云，支持切换到腾讯云。"""
        return await _get_doc_impl(url, ctx, cloud, tencent_product_id, lang)

    def _list_product_docs_impl(
        product_alias: str,
        cloud: str = "aliyun",
        keyword: str = "",
        limit: int = 0,
    ) -> List[Dict[str, str]]:
        """列出某个产品下的所有文档 - 内部实现。"""
        cloud_norm = (cloud or "aliyun").strip().lower()
        if cloud_norm == "tencent":
            try:
                product_id = services.extract_digits(product_alias)
                if not product_id:
                    return [{"error": f"无效的腾讯云 product_id: {product_alias}"}]
                crawler = TencentDocCrawler(request_delay=0.5)
                docs = crawler.discover_product_docs(
                    product_id=product_id,
                    keyword=keyword,
                    limit=limit,
                )
                if not docs:
                    return [{"error": f"未找到腾讯云产品 {product_id} 的文档目录"}]
                return [
                    {
                        "product_id": str(doc.get("product_id", "")),
                        "doc_id": str(doc.get("doc_id", "")),
                        "title": str(doc.get("title", "")),
                        "url": str(doc.get("url", "")),
                        "category": str(doc.get("category", "")),
                    }
                    for doc in docs
                ]
            except Exception as e:
                return [{"error": str(e)}]

        if cloud_norm != "aliyun":
            return [{"error": f"不支持的 cloud: {cloud}，当前仅支持 aliyun 或 tencent"}]

        try:
            crawler = DocumentCrawler(request_delay=0.5)
            menu_data = crawler.fetch_menu(product_alias)
            if menu_data is None:
                return [{"error": "无法获取产品目录"}]

            aliases = crawler.extract_aliases_from_menu(menu_data)
            return [{"alias": a, "url": f"https://help.aliyun.com/zh{a}"} for a in aliases]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def list_product_docs(
        product_alias: str,
        cloud: str = "aliyun",
        keyword: str = "",
        limit: int = 0,
    ) -> List[Dict[str, str]]:
        """列出某个产品下的所有文档。"""
        return _list_product_docs_impl(product_alias, cloud, keyword, limit)

    @mcp.tool()
    async def smart_search_aliyun_docs(
        query: str,
        ctx: Context,
        product_alias: str = "",
        limit: int = 20,
        with_summary: bool = False,
    ) -> str:
        """智能搜索阿里云文档，由 AI 自动识别过滤相关结果，支持多模态图片理解。
        
        Args:
            query: 搜索关键词，如 "安全组"、"VPC 路由表" 等
            product_alias: 产品 alias（可选），如 /vpc、/ecs。如果不指定，AI 会自动识别
            limit: 最大返回文档数，默认 20
            with_summary: 是否生成 AI 摘要（含图片理解，需要更长时间）
        
        Returns:
            搜索结果，包含文档列表和可选的功能点摘要
        """
        import re
        from bs4 import BeautifulSoup as BS
        
        summarizer = services.get_summarizer()
        crawler = DocumentCrawler(request_delay=0.3)
        
        total_steps = 5 if with_summary else 3
        await ctx.report_progress(0, total_steps, f"搜索阿里云文档: {query}")
        
        # 如果没有指定产品，AI 自动识别
        if not product_alias:
            product_prompt = (
                f"用户想查找的阿里云文档：「{query}」\n\n"
                f"请判断这个查询对应阿里云哪个产品，返回该产品的 alias 路径前缀。\n"
                f"常见产品 alias 示例：/ecs、/vpc、/oss、/rds、/slb、/cdn、/ram、/redis、/nas、/ack、/dns、/arms、/fc 等。\n"
                f"只输出一个 alias 路径，如 /ecs，不要输出其他内容。"
            )
            try:
                product_alias = summarizer.llm.generate(product_prompt, max_tokens=20).strip()
                match = re.search(r"(/[a-z][a-z0-9_-]*)", product_alias.lower())
                product_alias = match.group(1) if match else f"/{query.lower()}"
            except Exception as e:
                return f"AI 识别产品失败: {e}"
        
        # 获取文档目录
        await ctx.report_progress(1, total_steps, f"获取 {product_alias} 文档目录...")
        aliases = crawler.discover_product_docs(product_alias)
        if not aliases:
            return f"未找到产品 {product_alias} 的文档目录"
        
        # AI 筛选相关文档
        toc_lines = [f"{i}. {alias}" for i, alias in enumerate(aliases[:100])]  # 最多100篇
        toc_text = "\n".join(toc_lines)
        
        filter_prompt = f"""用户搜索: "{query}"

以下是阿里云 {product_alias} 产品的文档目录：
{toc_text}

请从以上列表中筛选出与用户搜索 "{query}" 真正相关的文档。
注意排除不相关的文档。

只输出相关文档的编号，用逗号分隔。如果没有相关文档，输出"无"。
示例输出: 0,2,5,8"""

        try:
            ai_response = summarizer.llm.generate(filter_prompt, max_tokens=200)
            if "无" in ai_response:
                return f"AI 判断 {product_alias} 文档中没有与 '{query}' 直接相关的文档"
            
            numbers = re.findall(r"\d+", ai_response)
            selected_indices = [int(n) for n in numbers if int(n) < len(aliases)][:limit]
        except Exception as e:
            logging.warning(f"AI 筛选失败: {e}，返回全部结果")
            selected_indices = list(range(min(10, len(aliases))))
        
        if not selected_indices:
            return f"AI 未能从 {product_alias} 文档中识别出与 '{query}' 相关的文档"
        
        selected_aliases = [aliases[i] for i in selected_indices]
        
        await ctx.report_progress(2, total_steps, f"获取 {len(selected_aliases)} 篇文档详情...")
        
        # 获取文档详情
        doc_details = []
        for alias in selected_aliases:
            try:
                data = crawler.fetch_doc_by_alias(alias)
                if data and data.get("title") and data.get("content"):
                    text = BS(data["content"], "lxml").get_text(separator="\n", strip=True)
                    doc_details.append({
                        "title": data.get("title", ""),
                        "url": f"https://help.aliyun.com/zh{alias}",
                        "text": text,
                    })
            except Exception as e:
                logging.debug(f"获取文档失败 {alias}: {e}")
        
        # 构建结果
        results = [f"## 阿里云「{query}」相关文档 ({len(doc_details)} 篇)\n"]
        results.append(f"产品: {product_alias}\n")
        
        for doc in doc_details:
            title = doc.get("title", "")
            url = doc.get("url", "")
            results.append(f"- [{title}]({url})")
        
        # 可选：生成 AI 功能点摘要（含多模态图片理解）
        if with_summary and doc_details:
            await ctx.report_progress(3, total_steps, "获取原始内容...")
            
            # 重新获取带图片的原始内容
            raw_contents = []
            for alias in selected_aliases[:5]:
                try:
                    data = crawler.fetch_doc_by_alias(alias)
                    if data and data.get("content"):
                        raw_contents.append({
                            "title": data.get("title", ""),
                            "content": data.get("content", ""),
                        })
                except Exception:
                    pass
            
            if raw_contents:
                await ctx.report_progress(4, total_steps, "生成功能点摘要（含图片理解）...")
                
                # 合并所有文档内容
                combined_content = ""
                for doc in raw_contents:
                    title = doc.get("title", "")
                    # 提取纯文本
                    text = BS(doc.get("content", ""), "lxml").get_text(separator="\n", strip=True)[:2000]
                    combined_content += f"\n\n### {title}\n{text}"
                
                summary_prompt = f"""请根据以下阿里云「{query}」相关文档内容，总结主要功能点：

{combined_content[:8000]}

请用简洁的列表形式总结 5-10 个核心功能点，每个功能点用一句话描述。
如果文档中有架构图或流程图，请描述图中的主要组件和关系。"""

                try:
                    # 提取所有图片
                    from .summarizer import extract_image_urls
                    all_images = []
                    for doc in raw_contents:
                        images = extract_image_urls(doc.get("content", ""))
                        all_images.extend(images)
                    
                    # 使用多模态生成摘要
                    if all_images and summarizer._is_multimodal:
                        summary = summarizer.llm.generate_with_images(
                            summary_prompt, all_images[:5], max_tokens=1000
                        )
                    else:
                        summary = summarizer.llm.generate(summary_prompt, max_tokens=1000)
                    
                    results.append(f"\n\n## 功能点摘要\n{summary}")
                except Exception as e:
                    logging.error(f"生成摘要失败: {e}")
                    results.append(f"\n\n## 功能点摘要\n生成失败: {e}")
        
        await ctx.report_progress(total_steps, total_steps)
        return "\n".join(results)

    @mcp.tool()
    def list_tencent_product_docs(
        product_id: str,
        keyword: str = "",
        limit: int = 0,
    ) -> List[Dict[str, str]]:
        """列出腾讯云某产品下的文档目录。"""
        return _list_product_docs_impl(product_id, "tencent", keyword, limit)

    @mcp.tool()
    async def smart_search_tencent_docs(
        query: str,
        ctx: Context,
        limit: int = 50,
        with_summary: bool = False,
    ) -> str:
        """智能搜索腾讯云文档，由 AI 自动识别过滤相关结果，支持多模态图片理解。
        
        Args:
            query: 搜索关键词，如 "弹性网卡"、"VPC 路由表" 等
            limit: 搜索数量限制，默认 50
            with_summary: 是否生成 AI 摘要（含图片理解，需要更长时间）
        """
        import requests
        from urllib.parse import quote
        
        summarizer = services.get_summarizer()
        tencent_crawler = TencentDocCrawler(request_delay=0.3)
        
        total_steps = 5 if with_summary else 3
        await ctx.report_progress(0, total_steps, f"搜索腾讯云文档: {query}")
        
        # 使用搜索 API 获取文档列表
        all_docs = []
        page = 1
        max_pages = min(limit // 10 + 1, 10)
        
        while page <= max_pages and len(all_docs) < limit:
            payload = {
                "action": "startup",
                "payload": {
                    "type": 7,
                    "keyword": query,
                    "page": page,
                    "preferSynonym": True,
                    "filter": {},
                    "sort": None
                }
            }
            try:
                encoded_query = quote(query, safe='')
                resp = requests.post(
                    "https://cloud.tencent.com/portal/search/api/result/startup",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0",
                        "Referer": f"https://cloud.tencent.com/search/{encoded_query}/7_1",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json().get("data", {})
                docs = data.get("list", [])
                if not docs:
                    break
                all_docs.extend(docs)
                page += 1
            except Exception as e:
                logging.error(f"搜索腾讯云文档失败: {e}")
                break
        
        if not all_docs:
            return f"未找到与 '{query}' 相关的腾讯云文档"
        
        await ctx.report_progress(1, 3, f"AI 筛选 {len(all_docs)} 篇文档...")
        
        # 构建文档列表供 AI 筛选
        doc_list_text = []
        for i, doc in enumerate(all_docs[:limit]):
            title = doc.get("title", "")
            product = doc.get("productName", "")
            url = doc.get("url", "")
            doc_list_text.append(f"{i}. 【{product}】{title} - {url}")
        
        filter_prompt = f"""用户搜索: "{query}"

以下是搜索到的腾讯云文档列表：
{chr(10).join(doc_list_text)}

请从以上列表中筛选出与用户搜索 "{query}" 真正相关的文档。
注意排除不相关的文档（如搜索"弹性网卡"时要排除"弹性公网IP"、"弹性伸缩"等不相关内容）。

只输出相关文档的编号，用逗号分隔。如果没有相关文档，输出"无"。
示例输出: 0,2,5,8"""

        try:
            ai_response = summarizer.llm.generate(filter_prompt, max_tokens=200)
            if "无" in ai_response:
                return f"AI 判断搜索结果中没有与 '{query}' 直接相关的文档"
            
            import re
            numbers = re.findall(r"\d+", ai_response)
            selected_indices = [int(n) for n in numbers if int(n) < len(all_docs)]
        except Exception as e:
            logging.warning(f"AI 筛选失败: {e}，返回全部结果")
            selected_indices = list(range(min(10, len(all_docs))))
        
        if not selected_indices:
            return f"AI 未能从搜索结果中识别出与 '{query}' 相关的文档"
        
        await ctx.report_progress(2, 3, f"整理 {len(selected_indices)} 篇相关文档...")
        
        # 构建结果
        results = [f"## 腾讯云「{query}」相关文档 ({len(selected_indices)} 篇)\n"]
        
        # 按产品分组
        by_product: Dict[str, List] = {}
        for idx in selected_indices:
            doc = all_docs[idx]
            product = doc.get("productName", "其他")
            if product not in by_product:
                by_product[product] = []
            by_product[product].append(doc)
        
        for product, docs in by_product.items():
            results.append(f"\n### {product}")
            for doc in docs:
                title = doc.get("title", "")
                url = doc.get("url", "")
                results.append(f"- [{title}]({url})")
        
        # 可选：生成 AI 功能点摘要（含多模态图片理解）
        if with_summary and selected_indices:
            await ctx.report_progress(3, total_steps, "获取文档详情...")
            
            # 获取文档详情
            doc_details = []
            for idx in selected_indices[:5]:  # 最多取 5 篇
                doc_info = all_docs[idx]
                url = doc_info.get("url", "")
                # 从 URL 提取 product_id 和 doc_id
                import re
                match = re.search(r"/product/(\d+)/(\d+)", url)
                if match:
                    pid, did = match.group(1), match.group(2)
                    detail = tencent_crawler.fetch_doc(doc_id=did, product_id=pid)
                    if detail and detail.get("text"):
                        doc_details.append(detail)
            
            if doc_details:
                await ctx.report_progress(4, total_steps, "生成功能点摘要（含图片理解）...")
                
                # 合并所有文档内容
                combined_content = ""
                for doc in doc_details:
                    title = doc.get("title", "")
                    text = doc.get("text", "")[:2000]
                    combined_content += f"\n\n### {title}\n{text}"
                
                summary_prompt = f"""请根据以下腾讯云「{query}」相关文档内容，总结主要功能点：

{combined_content[:8000]}

请用简洁的列表形式总结 5-10 个核心功能点，每个功能点用一句话描述。
如果文档中有架构图或流程图，请描述图中的主要组件和关系。"""

                try:
                    # 提取所有图片
                    from .summarizer import extract_image_urls
                    all_images = []
                    for doc in doc_details:
                        images = extract_image_urls(doc.get("text", ""))
                        all_images.extend(images)
                    
                    # 使用多模态生成摘要
                    if all_images and summarizer._is_multimodal:
                        summary = summarizer.llm.generate_with_images(
                            summary_prompt, all_images[:5], max_tokens=1000
                        )
                    else:
                        summary = summarizer.llm.generate(summary_prompt, max_tokens=1000)
                    
                    results.append(f"\n\n## 功能点摘要\n{summary}")
                except Exception as e:
                    logging.error(f"生成摘要失败: {e}")
                    results.append(f"\n\n## 功能点摘要\n生成失败: {e}")
        
        await ctx.report_progress(total_steps, total_steps)
        return "\n".join(results)

    @mcp.tool()
    async def get_tencent_doc(
        doc_ref: str,
        ctx: Context,
        product_id: str = "",
        lang: str = "zh",
    ) -> str:
        """获取腾讯云文档内容。支持文档 URL 或文档 ID。"""
        return await _get_doc_impl(doc_ref, ctx, "tencent", product_id, lang)

    @mcp.tool()
    async def smart_search_volcano_docs(
        query: str,
        ctx: Context,
        product_name: str = "",
        limit: int = 20,
        with_summary: bool = False,
    ) -> str:
        """智能搜索火山云文档，支持多模态图片理解。
        
        Args:
            query: 搜索关键词，如 "VPN"、"私有网络" 等
            product_name: 产品名称过滤（可选），如 "私有网络"、"云企业网"
            limit: 最大返回文档数，默认 20
            with_summary: 是否生成 AI 摘要（需要更长时间）
        
        Returns:
            搜索结果，包含文档列表和可选的功能点摘要
        """
        from .volcano_crawler import VolcanoDocCrawler, VOLCANO_KNOWN_LIBS
        
        summarizer = services.get_summarizer()
        crawler = VolcanoDocCrawler(request_delay=0.3)
        
        await ctx.report_progress(0, 4, f"搜索火山云文档: {query}")

        search_limit = max(limit * 2, 20)
        all_docs = []

        # 直接走 searchAll：将 Query 作为主检索词（例如 query="vpn"）
        search_query = (query or "").strip()
        if product_name and product_name.strip():
            # 仅作为可选增强关键词，不做 lib_id 预解析
            pn = product_name.strip()
            if pn.lower() not in search_query.lower():
                search_query = f"{search_query} {pn}".strip()

        all_docs = crawler.search_docs(query=search_query, limit=search_limit)

        # 无产品约束时，searchAll 失败再回退旧逻辑
        if not all_docs and not product_name:
            products_to_search = list(VOLCANO_KNOWN_LIBS.values())
            for product in products_to_search:
                docs = crawler.discover_product_docs(product, limit=search_limit)
                all_docs.extend(docs)
                if len(all_docs) >= search_limit:
                    break

        # 去重，避免不同来源重复文档
        dedup_docs = []
        seen = set()
        for doc in all_docs:
            key = doc.get("url") or f"{doc.get('lib_id', '')}/{doc.get('doc_id', '')}"
            if key in seen:
                continue
            seen.add(key)
            dedup_docs.append(doc)
        all_docs = dedup_docs
        
        if not all_docs:
            return f"未找到与 '{query}' 相关的火山云文档"
        
        await ctx.report_progress(1, 4, f"AI 筛选 {len(all_docs)} 篇文档...")
        
        # 构建文档列表供 AI 筛选
        doc_list_text = []
        for i, doc in enumerate(all_docs[:search_limit]):
            name = doc.get("name", "")
            url = doc.get("url", "")
            doc_list_text.append(f"{i}. {name} - {url}")
        
        filter_prompt = f"""用户搜索: "{query}"

以下是火山云文档列表：
{chr(10).join(doc_list_text)}

请从以上列表中筛选出与用户搜索 "{query}" 真正相关的文档。
注意：
- 排除不相关的文档
- 优先选择功能说明、产品介绍、最佳实践类文档
- 如果搜索的是产品名（如"VPN"），选择该产品的核心文档

只输出相关文档的编号，用逗号分隔。如果没有相关文档，输出"无"。
示例输出: 0,2,5,8"""

        try:
            ai_response = summarizer.llm.generate(filter_prompt, max_tokens=200)
            if "无" in ai_response:
                return f"AI 判断搜索结果中没有与 '{query}' 直接相关的文档"
            
            import re
            numbers = re.findall(r"\d+", ai_response)
            selected_indices = [int(n) for n in numbers if int(n) < len(all_docs)]
        except Exception as e:
            logging.warning(f"AI 筛选失败: {e}，返回全部结果")
            selected_indices = list(range(min(10, len(all_docs))))
        
        if not selected_indices:
            return f"AI 未能从搜索结果中识别出与 '{query}' 相关的文档"
        
        selected_docs = [all_docs[i] for i in selected_indices[:limit]]
        
        await ctx.report_progress(2, 4, f"获取 {len(selected_docs)} 篇文档详情...")
        
        # 获取文档详情
        doc_details = []
        for doc in selected_docs:
            lib_id = doc.get("lib_id", "")
            doc_id = doc.get("doc_id", "")
            if lib_id and doc_id:
                detail = crawler.fetch_doc(lib_id, doc_id)
                if detail:
                    doc_details.append(detail)
        
        # 构建结果
        results = [f"## 火山云「{query}」相关文档 ({len(doc_details)} 篇)\n"]
        
        for doc in doc_details:
            title = doc.get("title", "")
            url = doc.get("url", "")
            results.append(f"- [{title}]({url})")
        
        # 可选：生成 AI 功能点摘要
        if with_summary and doc_details:
            await ctx.report_progress(3, 4, "生成功能点摘要（含图片理解）...")
            
            # 合并所有文档内容
            combined_content = ""
            for doc in doc_details[:5]:  # 最多取 5 篇
                title = doc.get("title", "")
                text = doc.get("text", "")[:2000]  # 每篇最多 2000 字符
                combined_content += f"\n\n### {title}\n{text}"
            
            summary_prompt = f"""请根据以下火山云「{query}」相关文档内容，总结主要功能点：

{combined_content[:8000]}

请用简洁的列表形式总结 5-10 个核心功能点，每个功能点用一句话描述。
如果文档中有架构图，请描述图中的主要组件和关系。"""

            try:
                # 提取所有图片
                from .summarizer import extract_image_urls
                all_images = []
                for doc in doc_details[:5]:
                    images = extract_image_urls(doc.get("text", ""))
                    all_images.extend(images)
                
                # 使用多模态生成摘要
                if all_images and summarizer._is_multimodal:
                    summary = summarizer.llm.generate_with_images(
                        summary_prompt, all_images[:5], max_tokens=1000
                    )
                else:
                    summary = summarizer.llm.generate(summary_prompt, max_tokens=1000)
                
                results.append(f"\n\n## 功能点摘要\n{summary}")
            except Exception as e:
                logging.error(f"生成摘要失败: {e}")
                results.append(f"\n\n## 功能点摘要\n生成失败: {e}")
        
        await ctx.report_progress(4, 4)
        return "\n".join(results)

    @mcp.tool()
    async def smart_search_baidu_docs(
        query: str,
        ctx: Context,
        product: str = "",
        limit: int = 20,
        with_summary: bool = False,
    ) -> str:
        """智能搜索百度云文档，由 AI 自动识别过滤相关结果，支持多模态图片理解。
        
        Args:
            query: 搜索关键词，如 "安全组"、"VPC 路由表" 等
            product: 产品名称（可选），如 VPC、BCC、BOS。如果不指定，AI 会自动识别
            limit: 最大返回文档数，默认 20
            with_summary: 是否生成 AI 摘要（含图片理解，需要更长时间）
        
        Returns:
            搜索结果，包含文档列表和可选的功能点摘要
        """
        import re
        from .baidu_crawler import BaiduDocCrawler
        
        summarizer = services.get_summarizer()
        crawler = BaiduDocCrawler(request_delay=0.3)
        
        total_steps = 5 if with_summary else 3
        await ctx.report_progress(0, total_steps, f"搜索百度云文档: {query}")
        
        # 如果没有指定产品，AI 自动识别
        if not product:
            product_prompt = (
                f"用户想查找的百度云文档：「{query}」\n\n"
                f"请判断这个查询对应百度云哪个产品，返回该产品的代号（大写）。\n"
                f"常见产品代号示例：BCC（云服务器）、VPC（私有网络）、BOS（对象存储）、RDS（云数据库）、"
                f"BLB（负载均衡）、CDN（内容分发）、DNS（云解析）、CSN（云智能网）等。\n"
                f"只输出一个产品代号，如 VPC，不要输出其他内容。"
            )
            try:
                product = summarizer.llm.generate(product_prompt, max_tokens=20).strip().upper()
                match = re.search(r"([A-Z][A-Z0-9_-]*)", product)
                product = match.group(1) if match else query.upper()
            except Exception as e:
                return f"AI 识别产品失败: {e}"
        
        # 获取文档目录
        await ctx.report_progress(1, total_steps, f"获取 {product} 文档目录...")
        docs_list = crawler.discover_product_docs(product)
        if not docs_list:
            return f"未找到产品 {product} 的文档目录"
        
        # AI 筛选相关文档
        toc_lines = [f"{i}. {doc.get('title', doc.get('slug', ''))}" for i, doc in enumerate(docs_list[:100])]
        toc_text = "\n".join(toc_lines)
        
        filter_prompt = f"""用户搜索: "{query}"

以下是百度云 {product} 产品的文档目录：
{toc_text}

请从以上列表中筛选出与用户搜索 "{query}" 真正相关的文档。
注意排除不相关的文档。

只输出相关文档的编号，用逗号分隔。如果没有相关文档，输出"无"。
示例输出: 0,2,5,8"""

        try:
            ai_response = summarizer.llm.generate(filter_prompt, max_tokens=200)
            if "无" in ai_response:
                return f"AI 判断 {product} 文档中没有与 '{query}' 直接相关的文档"
            
            numbers = re.findall(r"\d+", ai_response)
            selected_indices = [int(n) for n in numbers if int(n) < len(docs_list)][:limit]
        except Exception as e:
            logging.warning(f"AI 筛选失败: {e}，返回全部结果")
            selected_indices = list(range(min(10, len(docs_list))))
        
        if not selected_indices:
            return f"AI 未能从 {product} 文档中识别出与 '{query}' 相关的文档"
        
        selected_docs = [docs_list[i] for i in selected_indices]
        
        await ctx.report_progress(2, total_steps, f"获取 {len(selected_docs)} 篇文档详情...")
        
        # 获取文档详情
        doc_details = []
        for doc_info in selected_docs:
            slug = doc_info.get("slug", "") if isinstance(doc_info, dict) else doc_info
            if not slug:
                continue
            try:
                doc_data = crawler.fetch_doc(product, slug)
                if doc_data and doc_data.get("text"):
                    doc_details.append({
                        "title": doc_data.get("title", ""),
                        "url": doc_data.get("url", f"https://cloud.baidu.com/doc/{product}/{slug}"),
                        "text": doc_data.get("text", ""),
                    })
            except Exception as e:
                logging.debug(f"获取文档失败 {slug}: {e}")
        
        # 构建结果
        results = [f"## 百度云「{query}」相关文档 ({len(doc_details)} 篇)\n"]
        results.append(f"产品: {product}\n")
        
        for doc in doc_details:
            title = doc.get("title", "")
            url = doc.get("url", "")
            results.append(f"- [{title}]({url})")
        
        # 可选：生成 AI 功能点摘要（含多模态图片理解）
        if with_summary and doc_details:
            await ctx.report_progress(3, total_steps, "准备内容...")
            
            # 合并所有文档内容
            combined_content = ""
            for doc in doc_details[:5]:  # 最多取 5 篇
                title = doc.get("title", "")
                text = doc.get("text", "")[:2000]
                combined_content += f"\n\n### {title}\n{text}"
            
            await ctx.report_progress(4, total_steps, "生成功能点摘要（含图片理解）...")
            
            summary_prompt = f"""请根据以下百度云「{query}」相关文档内容，总结主要功能点：

{combined_content[:8000]}

请用简洁的列表形式总结 5-10 个核心功能点，每个功能点用一句话描述。
如果文档中有架构图或流程图，请描述图中的主要组件和关系。"""

            try:
                # 提取所有图片
                from .summarizer import extract_image_urls
                all_images = []
                for doc in doc_details[:5]:
                    images = extract_image_urls(doc.get("text", ""))
                    all_images.extend(images)
                
                # 使用多模态生成摘要
                if all_images and summarizer._is_multimodal:
                    summary = summarizer.llm.generate_with_images(
                        summary_prompt, all_images[:5], max_tokens=1000
                    )
                else:
                    summary = summarizer.llm.generate(summary_prompt, max_tokens=1000)
                
                results.append(f"\n\n## 功能点摘要\n{summary}")
            except Exception as e:
                logging.error(f"生成摘要失败: {e}")
                results.append(f"\n\n## 功能点摘要\n生成失败: {e}")
        
        await ctx.report_progress(total_steps, total_steps)
        return "\n".join(results)

    @mcp.tool()
    async def get_baidu_doc(
        product: str,
        slug: str,
        ctx: Context,
    ) -> str:
        """获取百度云文档内容。
        
        Args:
            product: 产品代号（大写），如 VPC、BCC、BOS
            slug: 文档 slug，如 "overview-53"
        
        Returns:
            文档内容
        """
        from .baidu_crawler import BaiduDocCrawler
        
        await ctx.report_progress(0, 1, f"获取 {product}/{slug}...")
        
        crawler = BaiduDocCrawler(request_delay=0.3)
        doc_data = crawler.fetch_doc(product, slug)
        
        if not doc_data:
            return f"获取文档失败: {product}/{slug}"
        
        await ctx.report_progress(1, 1)
        
        title = doc_data.get("title", "")
        url = doc_data.get("url", "")
        text = doc_data.get("text", "")
        
        return f"# {title}\n\n**URL**: {url}\n\n{text}"

    @mcp.tool()
    async def get_volcano_doc(
        doc_ref: str,
        ctx: Context,
        lib_id: str = "",
    ) -> str:
        """获取火山云文档内容。
        
        Args:
            doc_ref: 文档 URL 或文档 ID
            lib_id: 产品 LibID（如果 doc_ref 是纯数字 ID 则必填）
        
        Returns:
            文档内容（Markdown 格式）
        """
        from .volcano_crawler import VolcanoDocCrawler
        import re
        
        await ctx.report_progress(0, 2, "解析文档引用...")
        
        raw = (doc_ref or "").strip()
        if not raw:
            return "请提供文档 URL 或文档 ID"
        
        # 解析 URL: https://www.volcengine.com/docs/6401/69467
        match = re.search(r"/docs/(\d+)/(\d+)", raw)
        if match:
            lib_id = match.group(1)
            doc_id = match.group(2)
        elif raw.isdigit() and lib_id:
            doc_id = raw
        else:
            return f"无法解析文档引用: {doc_ref}。请提供完整 URL 或同时提供 lib_id 和 doc_id"
        
        await ctx.report_progress(1, 2, f"获取文档 {lib_id}/{doc_id}...")
        
        crawler = VolcanoDocCrawler(request_delay=0.3)
        detail = crawler.fetch_doc(lib_id, doc_id)
        
        if not detail:
            return f"获取文档失败: {lib_id}/{doc_id}"
        
        await ctx.report_progress(2, 2)
        
        title = detail.get("title", "")
        url = detail.get("url", "")
        text = detail.get("text", "")
        
        return f"# {title}\n\n**URL**: {url}\n\n{text}"

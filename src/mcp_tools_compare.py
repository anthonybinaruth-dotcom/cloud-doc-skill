"""MCP 云厂商文档对比工具。"""

from __future__ import annotations

import logging

from fastmcp import Context, FastMCP

from .baidu_crawler import BaiduDocCrawler
from .crawler import DocumentCrawler
from .mcp_services import AppServices
from .tencent_crawler import TencentDocCrawler


def register_compare_tools(mcp: FastMCP, services: AppServices) -> None:
    @mcp.tool()
    async def compare_cloud_docs(
        ctx: Context,
        product: str = "VPC",
        aliyun_alias: str = "",
        baidu_slug: str = "",
    ) -> str:
        """对比阿里云和百度云同一产品的文档内容，用AI生成对比分析。"""
        try:
            import re

            summarizer = services.get_summarizer()
            aliyun_crawler = DocumentCrawler(request_delay=0.3)
            baidu_crawler = BaiduDocCrawler(request_delay=0.3)

            await ctx.report_progress(0, 5, "AI 识别产品...")
            mapping_prompt = (
                f"用户想对比的云产品/功能：「{product}」\n\n"
                f"请判断这个查询分别对应阿里云和百度云的哪个产品。\n"
                f"阿里云产品alias格式为 /产品名（小写），如 /ecs、/vpc、/oss\n"
                f"百度云产品名格式为大写，如 BCC、VPC、BOS\n\n"
                f"如果用户查询的是某个子功能（如安全组、路由表、存储桶），请返回它所属的主产品。\n\n"
                f"请严格按以下格式输出，不要输出其他内容：\n"
                f"aliyun:/xxx\n"
                f"baidu:YYY\n"
                f"feature:具体功能名（如果是子功能的话，否则留空）"
            )

            try:
                mapping_response = summarizer.llm.generate(mapping_prompt, max_tokens=100)
                aliyun_match = re.search(r"aliyun:\s*(/[a-z][a-z0-9_-]*)", mapping_response.lower())
                baidu_match = re.search(r"baidu:\s*([a-z][a-z0-9_-]*)", mapping_response.lower())
                feature_match = re.search(r"feature:\s*(.+)", mapping_response, re.IGNORECASE)

                aliyun_product = aliyun_match.group(1) if aliyun_match else f"/{product.lower()}"
                baidu_product = baidu_match.group(1).upper() if baidu_match else product.upper()
                feature_name = feature_match.group(1).strip() if feature_match and feature_match.group(1).strip() else ""
            except Exception:
                aliyun_product = f"/{product.lower()}"
                baidu_product = product.upper()
                feature_name = ""

            display_name = feature_name if feature_name else f"{aliyun_product.strip('/')}/{baidu_product}"

            if aliyun_alias and baidu_slug:
                await ctx.report_progress(1, 5, "获取阿里云文档...")
                aliyun_doc = aliyun_crawler.crawl_page(aliyun_alias)
                await ctx.report_progress(2, 5, "获取百度云文档...")
                baidu_doc = baidu_crawler.fetch_doc(baidu_product, baidu_slug)

                if not baidu_doc:
                    return f"获取百度云文档失败: {baidu_slug}"

                await ctx.report_progress(3, 5, "AI 生成对比分析...")
                prompt = (
                    "以下是阿里云和百度云关于同一类产品功能的文档。"
                    "请基于文档内容，对比两个云厂商在该功能上的**产品能力差异**，"
                    "而不是对比文档本身的写法差异。\n\n"
                    f"## 阿里云 - {aliyun_doc.title}\n{aliyun_doc.content[:3000]}\n\n"
                    f"## 百度云 - {baidu_doc['title']}\n{baidu_doc['text'][:3000]}\n\n"
                    "请输出：\n"
                    "1. 双方都支持的功能点\n"
                    "2. 阿里云独有的功能/能力\n"
                    "3. 百度云独有的功能/能力\n"
                    "4. 同一功能的参数/规格/限制差异\n"
                    "5. 总结：哪个厂商在该功能上更有优势，为什么"
                )
                comparison = summarizer.llm.generate(prompt, max_tokens=2000)

                await ctx.report_progress(5, 5)
                return (
                    "## 产品功能对比\n\n"
                    f"阿里云: {aliyun_doc.title} ({aliyun_doc.url})\n"
                    f"百度云: {baidu_doc['title']} ({baidu_doc['url']})\n\n"
                    f"{comparison}"
                )

            await ctx.report_progress(1, 5, "获取阿里云文档目录...")
            aliyun_aliases = aliyun_crawler.discover_product_docs(aliyun_product)
            await ctx.report_progress(2, 5, "获取百度云文档目录...")
            baidu_docs_list = baidu_crawler.discover_product_docs(baidu_product)

            if not aliyun_aliases:
                return f"未找到阿里云 {aliyun_product} 文档"
            if not baidu_docs_list:
                return f"未找到百度云 {baidu_product} 文档"

            if feature_name:
                aliyun_toc = "\n".join(f"{i}. {a}" for i, a in enumerate(aliyun_aliases))
                select_prompt = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是阿里云产品文档目录：\n{aliyun_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp = summarizer.llm.generate(select_prompt, max_tokens=80)
                    nums = re.findall(r"\d+", resp)
                    aliyun_selected = [aliyun_aliases[int(n)] for n in nums if int(n) < len(aliyun_aliases)][:10]
                except Exception:
                    aliyun_selected = aliyun_aliases[:10]

                baidu_toc = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(baidu_docs_list))
                select_prompt2 = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是百度云产品文档目录：\n{baidu_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp2 = summarizer.llm.generate(select_prompt2, max_tokens=80)
                    nums2 = re.findall(r"\d+", resp2)
                    baidu_selected = [baidu_docs_list[int(n)] for n in nums2 if int(n) < len(baidu_docs_list)][:10]
                except Exception:
                    baidu_selected = baidu_docs_list[:10]
            else:
                aliyun_selected = aliyun_aliases[:10]
                baidu_selected = baidu_docs_list[:10]

            results = [
                f"## 阿里云 vs 百度云「{display_name}」产品功能对比\n",
                f"阿里云文档数: {len(aliyun_aliases)}",
                f"百度云文档数: {len(baidu_docs_list)}\n",
            ]

            await ctx.report_progress(3, 5, "获取文档内容...")
            from bs4 import BeautifulSoup as BS

            aliyun_contents = []
            for alias in aliyun_selected:
                data = aliyun_crawler.fetch_doc_by_alias(alias)
                if data and data.get("title") and data.get("content"):
                    text = BS(data["content"], "lxml").get_text(separator="\n", strip=True)
                    aliyun_contents.append(f"【{data['title']}】\n{text[:1200]}")

            baidu_contents = []
            for doc_info in baidu_selected:
                slug = doc_info["slug"] if isinstance(doc_info, dict) else doc_info
                doc = baidu_crawler.fetch_doc(baidu_product, slug)
                if doc and doc.get("text"):
                    baidu_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            if not aliyun_contents or not baidu_contents:
                results.append("无法获取足够的文档内容进行对比")
                return "\n".join(results)

            aliyun_detail = "\n\n".join(aliyun_contents)
            baidu_detail = "\n\n".join(baidu_contents)

            await ctx.report_progress(4, 5, "AI 生成对比分析...")
            compare_target = feature_name if feature_name else display_name
            prompt = (
                f"你是一个云产品分析师。以下是阿里云和百度云关于「{compare_target}」的文档内容。\n"
                f"请基于这些信息，全面对比两个云厂商在「{compare_target}」上的**产品功能差异**。\n"
                f"注意：要对比的是产品功能本身的差异，不是文档写法的差异。\n\n"
                f"## 阿里云文档内容\n{aliyun_detail[:5000]}\n\n"
                f"## 百度云文档内容\n{baidu_detail[:5000]}\n\n"
                "请按以下结构输出对比结果：\n"
                "1. **双方都支持的功能**：列出共有功能及各自的规格参数差异\n"
                "2. **阿里云独有功能**：百度云不具备的功能和能力\n"
                "3. **百度云独有功能**：阿里云不具备的功能和能力\n"
                "4. **配额与限制对比**：关键限制的差异\n"
                "5. **综合评价**：各自的优势领域和适用场景"
            )
            comparison = summarizer.llm.generate(prompt, max_tokens=3000)
            results.append(comparison)

            await ctx.report_progress(5, 5)
            return "\n".join(results)
        except Exception as e:
            logging.error(f"文档对比失败: {e}", exc_info=True)
            return f"文档对比失败: {e}"

    @mcp.tool()
    async def compare_tencent_baidu_docs(
        ctx: Context,
        product: str = "VPC",
        tencent_product_id: str = "",
        tencent_doc_ref: str = "",
        baidu_slug: str = "",
    ) -> str:
        """对比腾讯云和百度云同一产品的文档内容，用AI生成对比分析。"""
        try:
            import re

            summarizer = services.get_summarizer()
            tencent_crawler = TencentDocCrawler(request_delay=0.3)
            baidu_crawler = BaiduDocCrawler(request_delay=0.3)

            await ctx.report_progress(0, 5, "AI 识别产品...")
            mapping_prompt = (
                f"用户想对比的云产品/功能：「{product}」\n\n"
                f"请判断这个查询在百度云中对应哪个产品代号（大写，如 BCC、VPC、BOS），"
                f"并提取具体子功能（若无则留空）。\n\n"
                "请严格按以下格式输出，不要输出其他内容：\n"
                "baidu:YYY\n"
                "feature:具体功能名（如果是子功能的话，否则留空）"
            )
            try:
                mapping_response = summarizer.llm.generate(mapping_prompt, max_tokens=80)
                baidu_match = re.search(r"baidu:\s*([a-z][a-z0-9_-]*)", mapping_response.lower())
                feature_match = re.search(r"feature:\s*(.+)", mapping_response, re.IGNORECASE)
                baidu_product = baidu_match.group(1).upper() if baidu_match else product.upper()
                feature_name = feature_match.group(1).strip() if feature_match and feature_match.group(1).strip() else ""
            except Exception:
                baidu_product = product.upper()
                feature_name = ""

            display_name = feature_name if feature_name else f"{product}/百度云{baidu_product}"
            normalized_pid = services.extract_digits(tencent_product_id)

            if tencent_doc_ref and baidu_slug:
                parsed_pid, doc_id = services.parse_tencent_doc_ref(tencent_doc_ref, tencent_product_id)
                if not doc_id:
                    return f"无法解析腾讯云文档引用: {tencent_doc_ref}"

                await ctx.report_progress(1, 5, "获取腾讯云文档...")
                tencent_doc = tencent_crawler.fetch_doc(doc_id=doc_id, product_id=parsed_pid)
                await ctx.report_progress(2, 5, "获取百度云文档...")
                baidu_doc = baidu_crawler.fetch_doc(baidu_product, baidu_slug)

                if not tencent_doc:
                    return f"获取腾讯云文档失败: {tencent_doc_ref}"
                if not baidu_doc:
                    return f"获取百度云文档失败: {baidu_slug}"

                await ctx.report_progress(3, 5, "AI 生成对比分析...")
                prompt = (
                    "以下是腾讯云和百度云关于同一类产品功能的文档。"
                    "请基于文档内容，对比两个云厂商在该功能上的**产品能力差异**，"
                    "而不是对比文档写法差异。\n\n"
                    f"## 腾讯云 - {tencent_doc['title']}\n{tencent_doc['text'][:3000]}\n\n"
                    f"## 百度云 - {baidu_doc['title']}\n{baidu_doc['text'][:3000]}\n\n"
                    "请输出：\n"
                    "1. 双方都支持的功能点\n"
                    "2. 腾讯云独有的功能/能力\n"
                    "3. 百度云独有的功能/能力\n"
                    "4. 同一功能的参数/规格/限制差异\n"
                    "5. 总结：哪个厂商在该功能上更有优势，为什么"
                )
                comparison = summarizer.llm.generate(prompt, max_tokens=2200)

                await ctx.report_progress(5, 5)
                return (
                    "## 腾讯云 vs 百度云产品功能对比\n\n"
                    f"腾讯云: {tencent_doc['title']} ({tencent_doc['url']})\n"
                    f"百度云: {baidu_doc['title']} ({baidu_doc['url']})\n\n"
                    f"{comparison}"
                )

            if not normalized_pid:
                return "进行腾讯云 vs 百度云产品级对比时，需要提供 tencent_product_id（例如 VPC: 215）"

            await ctx.report_progress(1, 5, "获取腾讯云文档目录...")
            # 使用 product 参数作为搜索关键词，因为纯数字 ID 无法搜索到文档
            tencent_docs_list = tencent_crawler.discover_product_docs(
                normalized_pid, keyword=feature_name or product
            )
            await ctx.report_progress(2, 5, "获取百度云文档目录...")
            baidu_docs_list = baidu_crawler.discover_product_docs(baidu_product)

            if not tencent_docs_list:
                return f"未找到腾讯云产品 {normalized_pid} 文档"
            if not baidu_docs_list:
                return f"未找到百度云 {baidu_product} 文档"

            if feature_name:
                tencent_toc = "\n".join(
                    f"{i}. {d.get('title', '')} (doc_id:{d.get('doc_id', '')})"
                    for i, d in enumerate(tencent_docs_list)
                )
                select_prompt_tencent = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是腾讯云产品文档目录：\n{tencent_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp = summarizer.llm.generate(select_prompt_tencent, max_tokens=80)
                    nums = re.findall(r"\d+", resp)
                    tencent_selected = [tencent_docs_list[int(n)] for n in nums if int(n) < len(tencent_docs_list)][:10]
                except Exception:
                    tencent_selected = tencent_docs_list[:10]

                baidu_toc = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(baidu_docs_list))
                select_prompt_baidu = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是百度云产品文档目录：\n{baidu_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp2 = summarizer.llm.generate(select_prompt_baidu, max_tokens=80)
                    nums2 = re.findall(r"\d+", resp2)
                    baidu_selected = [baidu_docs_list[int(n)] for n in nums2 if int(n) < len(baidu_docs_list)][:10]
                except Exception:
                    baidu_selected = baidu_docs_list[:10]
            else:
                tencent_selected = tencent_docs_list[:10]
                baidu_selected = baidu_docs_list[:10]

            results = [
                f"## 腾讯云 vs 百度云「{display_name}」产品功能对比\n",
                f"腾讯云文档数: {len(tencent_docs_list)}",
                f"百度云文档数: {len(baidu_docs_list)}\n",
            ]

            await ctx.report_progress(3, 5, "获取文档内容...")
            tencent_contents = []
            for doc_info in tencent_selected:
                doc = tencent_crawler.fetch_doc(
                    doc_id=str(doc_info.get("doc_id", "")),
                    product_id=str(doc_info.get("product_id", normalized_pid)),
                )
                if doc and doc.get("text"):
                    tencent_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            baidu_contents = []
            for doc_info in baidu_selected:
                slug = doc_info["slug"] if isinstance(doc_info, dict) else doc_info
                doc = baidu_crawler.fetch_doc(baidu_product, slug)
                if doc and doc.get("text"):
                    baidu_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            if not tencent_contents or not baidu_contents:
                results.append("无法获取足够的文档内容进行对比")
                return "\n".join(results)

            tencent_detail = "\n\n".join(tencent_contents)
            baidu_detail = "\n\n".join(baidu_contents)

            await ctx.report_progress(4, 5, "AI 生成对比分析...")
            compare_target = feature_name if feature_name else display_name
            prompt = (
                f"你是一个云产品分析师。以下是腾讯云和百度云关于「{compare_target}」的文档内容。\n"
                f"请基于这些信息，全面对比两个云厂商在「{compare_target}」上的**产品功能差异**。\n"
                "注意：要对比的是产品功能本身的差异，不是文档写法差异。\n\n"
                f"## 腾讯云文档内容\n{tencent_detail[:5000]}\n\n"
                f"## 百度云文档内容\n{baidu_detail[:5000]}\n\n"
                "请按以下结构输出对比结果：\n"
                "1. **双方都支持的功能**：列出共有功能及各自的规格参数差异\n"
                "2. **腾讯云独有功能**：百度云不具备的功能和能力\n"
                "3. **百度云独有功能**：腾讯云不具备的功能和能力\n"
                "4. **配额与限制对比**：关键限制的差异\n"
                "5. **综合评价**：各自的优势领域和适用场景"
            )
            comparison = summarizer.llm.generate(prompt, max_tokens=3200)
            results.append(comparison)

            await ctx.report_progress(5, 5)
            return "\n".join(results)
        except Exception as e:
            logging.error(f"腾讯云与百度云文档对比失败: {e}", exc_info=True)
            return f"文档对比失败: {e}"

    @mcp.tool()
    async def compare_volcano_baidu_docs(
        ctx: Context,
        product: str = "VPC",
        volcano_product_name: str = "",
        baidu_slug: str = "",
    ) -> str:
        """对比火山云和百度云同一产品的文档内容，用AI生成对比分析。
        
        Args:
            product: 产品名称，如 "VPC", "私有网络", "安全组"
            volcano_product_name: 火山云产品名（可选），如 "私有网络"
            baidu_slug: 百度云文档slug（可选），如 "overview-53"
        """
        try:
            import re
            from .volcano_crawler import VolcanoDocCrawler

            summarizer = services.get_summarizer()
            volcano_crawler = VolcanoDocCrawler(request_delay=0.3)
            baidu_crawler = BaiduDocCrawler(request_delay=0.3)

            await ctx.report_progress(0, 5, "AI 识别产品...")
            mapping_prompt = (
                f"用户想对比的云产品/功能：「{product}」\n\n"
                f"请判断这个查询在火山云和百度云中分别对应哪个产品。\n"
                f"火山云产品名格式为中文，如 私有网络、云企业网、NAT网关\n"
                f"百度云产品名格式为大写，如 BCC、VPC、BOS\n\n"
                "请严格按以下格式输出，不要输出其他内容：\n"
                "volcano:火山云产品名\n"
                "baidu:YYY\n"
                "feature:具体功能名（如果是子功能的话，否则留空）"
            )
            try:
                mapping_response = summarizer.llm.generate(mapping_prompt, max_tokens=100)
                volcano_match = re.search(r"volcano:\s*(.+?)[\n$]", mapping_response)
                baidu_match = re.search(r"baidu:\s*([a-z][a-z0-9_-]*)", mapping_response.lower())
                feature_match = re.search(r"feature:\s*(.+)", mapping_response, re.IGNORECASE)

                volcano_product = volcano_match.group(1).strip() if volcano_match else volcano_product_name or "私有网络"
                baidu_product = baidu_match.group(1).upper() if baidu_match else product.upper()
                feature_name = feature_match.group(1).strip() if feature_match and feature_match.group(1).strip() else ""
            except Exception:
                volcano_product = volcano_product_name or "私有网络"
                baidu_product = product.upper()
                feature_name = ""

            display_name = feature_name if feature_name else f"火山云{volcano_product}/百度云{baidu_product}"

            await ctx.report_progress(1, 5, "获取火山云文档目录...")
            volcano_docs_list = volcano_crawler.discover_product_docs(volcano_product, limit=50)
            await ctx.report_progress(2, 5, "获取百度云文档目录...")
            baidu_docs_list = baidu_crawler.discover_product_docs(baidu_product)

            if not volcano_docs_list:
                return f"未找到火山云 {volcano_product} 文档"
            if not baidu_docs_list:
                return f"未找到百度云 {baidu_product} 文档"

            # 如果指定了子功能，用 AI 筛选相关文档
            if feature_name:
                volcano_toc = "\n".join(
                    f"{i}. {d.get('name', '')}"
                    for i, d in enumerate(volcano_docs_list)
                )
                select_prompt_volcano = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是火山云产品文档目录：\n{volcano_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp = summarizer.llm.generate(select_prompt_volcano, max_tokens=80)
                    nums = re.findall(r"\d+", resp)
                    volcano_selected = [volcano_docs_list[int(n)] for n in nums if int(n) < len(volcano_docs_list)][:10]
                except Exception:
                    volcano_selected = volcano_docs_list[:10]

                baidu_toc = "\n".join(f"{i}. {d['title']}" for i, d in enumerate(baidu_docs_list))
                select_prompt_baidu = (
                    f"用户想了解的功能：「{feature_name}」\n\n"
                    f"以下是百度云产品文档目录：\n{baidu_toc}\n\n"
                    f"请选出与「{feature_name}」最相关的5-10篇文档编号，用逗号分隔，不要输出其他内容。"
                )
                try:
                    resp2 = summarizer.llm.generate(select_prompt_baidu, max_tokens=80)
                    nums2 = re.findall(r"\d+", resp2)
                    baidu_selected = [baidu_docs_list[int(n)] for n in nums2 if int(n) < len(baidu_docs_list)][:10]
                except Exception:
                    baidu_selected = baidu_docs_list[:10]
            else:
                volcano_selected = volcano_docs_list[:10]
                baidu_selected = baidu_docs_list[:10]

            results = [
                f"## 火山云 vs 百度云「{display_name}」产品功能对比\n",
                f"火山云文档数: {len(volcano_docs_list)}",
                f"百度云文档数: {len(baidu_docs_list)}\n",
            ]

            await ctx.report_progress(3, 5, "获取文档内容...")
            volcano_contents = []
            for doc_info in volcano_selected:
                lib_id = doc_info.get("lib_id", "")
                doc_id = doc_info.get("doc_id", "")
                if lib_id and doc_id:
                    doc = volcano_crawler.fetch_doc(lib_id, doc_id)
                    if doc and doc.get("text"):
                        volcano_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            baidu_contents = []
            for doc_info in baidu_selected:
                slug = doc_info["slug"] if isinstance(doc_info, dict) else doc_info
                doc = baidu_crawler.fetch_doc(baidu_product, slug)
                if doc and doc.get("text"):
                    baidu_contents.append(f"【{doc['title']}】\n{doc['text'][:1200]}")

            if not volcano_contents or not baidu_contents:
                results.append("无法获取足够的文档内容进行对比")
                return "\n".join(results)

            volcano_detail = "\n\n".join(volcano_contents)
            baidu_detail = "\n\n".join(baidu_contents)

            await ctx.report_progress(4, 5, "AI 生成对比分析...")
            compare_target = feature_name if feature_name else display_name
            prompt = (
                f"你是一个云产品分析师。以下是火山云和百度云关于「{compare_target}」的文档内容。\n"
                f"请基于这些信息，全面对比两个云厂商在「{compare_target}」上的**产品功能差异**。\n"
                "注意：要对比的是产品功能本身的差异，不是文档写法差异。\n\n"
                f"## 火山云文档内容\n{volcano_detail[:5000]}\n\n"
                f"## 百度云文档内容\n{baidu_detail[:5000]}\n\n"
                "请按以下结构输出对比结果：\n"
                "1. **双方都支持的功能**：列出共有功能及各自的规格参数差异\n"
                "2. **火山云独有功能**：百度云不具备的功能和能力\n"
                "3. **百度云独有功能**：火山云不具备的功能和能力\n"
                "4. **配额与限制对比**：关键限制的差异\n"
                "5. **综合评价**：各自的优势领域和适用场景"
            )
            comparison = summarizer.llm.generate(prompt, max_tokens=3200)
            results.append(comparison)

            await ctx.report_progress(5, 5)
            return "\n".join(results)
        except Exception as e:
            logging.error(f"火山云与百度云文档对比失败: {e}", exc_info=True)
            return f"文档对比失败: {e}"


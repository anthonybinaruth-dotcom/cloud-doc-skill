"""变更检测模块"""

import difflib
import logging
from datetime import datetime
from typing import Dict, List, Set

from .models import ChangeReport, ChangeType, Document, DocumentChange


class ChangeDetector:
    """变更检测类"""

    # 无意义变更的关键词（用于过滤）
    NOISE_PATTERNS = [
        "copyright",
        "©",
        "all rights reserved",
        "last updated",
        "最后更新",
        "版权所有",
    ]

    def detect_changes(
        self, old_docs: List[Document], new_docs: List[Document]
    ) -> ChangeReport:
        """
        检测文档变更

        Args:
            old_docs: 上次检查的文档列表
            new_docs: 本次检查的文档列表

        Returns:
            变更报告
        """
        old_map: Dict[str, Document] = {doc.url: doc for doc in old_docs}
        new_map: Dict[str, Document] = {doc.url: doc for doc in new_docs}

        old_urls: Set[str] = set(old_map.keys())
        new_urls: Set[str] = set(new_map.keys())

        # 新增文档
        added_urls = new_urls - old_urls
        added = [new_map[url] for url in added_urls]

        # 删除文档
        deleted_urls = old_urls - new_urls
        deleted = [old_map[url] for url in deleted_urls]

        # 修改文档
        common_urls = old_urls & new_urls
        modified: List[DocumentChange] = []

        for url in common_urls:
            old_doc = old_map[url]
            new_doc = new_map[url]

            # 通过哈希快速判断是否变更
            if old_doc.content_hash != new_doc.content_hash:
                diff = self.compute_diff(old_doc.content, new_doc.content)

                # 过滤无意义变更
                if not self._is_noise_change(diff):
                    change_type = self.categorize_change(diff)
                    modified.append(
                        DocumentChange(
                            document=new_doc,
                            old_content_hash=old_doc.content_hash,
                            new_content_hash=new_doc.content_hash,
                            diff=diff,
                            change_type=change_type,
                        )
                    )

        report = ChangeReport(
            added=added,
            modified=modified,
            deleted=deleted,
            timestamp=datetime.now(),
        )

        logging.info(
            f"变更检测完成: 新增 {len(added)}, "
            f"修改 {len(modified)}, 删除 {len(deleted)}"
        )

        return report

    def compute_diff(self, old_content: str, new_content: str) -> str:
        """
        计算内容差异

        Args:
            old_content: 旧内容
            new_content: 新内容

        Returns:
            差异文本（unified diff格式）
        """
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines, new_lines, fromfile="旧版本", tofile="新版本", lineterm=""
        )

        return "\n".join(diff)

    def categorize_change(self, diff: str) -> ChangeType:
        """
        分类变更类型

        基于变更行数和内容特征判断变更类型：
        - MINOR: 少量文字修改（<10行变更）
        - MAJOR: 大量内容变更（>=10行变更）
        - STRUCTURAL: 结构性变化（标题、章节变更）

        Args:
            diff: 差异文本

        Returns:
            变更类型
        """
        lines = diff.split("\n")

        # 统计变更行数
        added_lines = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
        removed_lines = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
        total_changes = added_lines + removed_lines

        # 检查是否有结构性变化（标题变更）
        structural_markers = ["# ", "## ", "### ", "#### "]
        has_structural_change = any(
            any(line.lstrip("+-").startswith(marker) for marker in structural_markers)
            for line in lines
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        )

        if has_structural_change:
            return ChangeType.STRUCTURAL
        elif total_changes >= 10:
            return ChangeType.MAJOR
        else:
            return ChangeType.MINOR

    def _is_noise_change(self, diff: str) -> bool:
        """
        判断是否为无意义变更

        Args:
            diff: 差异文本

        Returns:
            是否为无意义变更
        """
        lines = diff.split("\n")

        # 获取实际变更的行
        changed_lines = [
            line
            for line in lines
            if (line.startswith("+") or line.startswith("-"))
            and not line.startswith(("+++", "---"))
        ]

        if not changed_lines:
            return True

        # 检查所有变更行是否都包含噪声关键词
        noise_count = 0
        for line in changed_lines:
            line_lower = line.lower()
            if any(pattern in line_lower for pattern in self.NOISE_PATTERNS):
                noise_count += 1

        # 如果所有变更行都是噪声，则认为是无意义变更
        return noise_count == len(changed_lines)

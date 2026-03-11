---
inclusion: manual
---

# Cloud Doc Skill 使用指南

当用户提到抓取云文档、检查文档更新、对比不同云厂商产品、生成变更报告时，使用本项目的 `cloud-doc-skill` CLI 工具。

## 调用方式

需要先设置 PATH（或直接用完整路径）：
```bash
export PATH="$PATH:/Users/ayizibaalimujiang/Library/Python/3.9/bin"
cloud-doc-skill <skill_name> '<params_json>'
```

工作目录必须在 `testab/` 下执行。

## 可用技能

### fetch_doc — 抓取文档
```bash
# 基本用法：按产品抓取
cloud-doc-skill fetch_doc '{"cloud": "aliyun", "product": "vpc", "max_pages": 5}'
cloud-doc-skill fetch_doc '{"cloud": "tencent", "product": "私有网络"}'
cloud-doc-skill fetch_doc '{"cloud": "baidu", "product": "VPC"}'
cloud-doc-skill fetch_doc '{"cloud": "volcano", "product": "私有网络"}'

# 使用关键词过滤（适用于产品下的特定功能模块）
cloud-doc-skill fetch_doc '{"cloud": "aliyun", "product": "ecs", "keyword": "弹性网卡", "max_pages": 20}'

# 直接抓取单篇文档
cloud-doc-skill fetch_doc '{"cloud": "aliyun", "doc_ref": "/ecs/user-guide/eni-overview"}'
```

**重要提示：** 
- 阿里云的文档结构按产品组织，功能模块（如"弹性网卡"）不是独立产品
- 如果抓取功能模块文档，应使用主产品名 + keyword 参数
- 例如：弹性网卡属于 ECS 产品，应使用 `"product": "ecs", "keyword": "弹性网卡"`

### check_changes — 检测变更
```bash
cloud-doc-skill check_changes '{"cloud": "aliyun", "product": "vpc", "days": 7}'
```

### compare_docs — 跨云对比
```bash
cloud-doc-skill compare_docs '{"left": {"cloud": "aliyun", "product": "vpc"}, "right": {"cloud": "tencent", "product": "私有网络"}}'
```

### run_monitor — 批量巡检
```bash
cloud-doc-skill run_monitor '{"clouds": ["aliyun", "tencent"], "products": ["vpc"], "days": 1}'
```

## cloud 参数值
- 阿里云: `aliyun`
- 腾讯云: `tencent`
- 百度云: `baidu`
- 火山引擎: `volcano`

## 注意事项
- AI 摘要功能需要设置 `LLM_API_KEY` 环境变量
- 不需要摘要时用 `"with_summary": false`
- 工作目录应在 `testab/` 下执行

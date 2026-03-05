# 云文档监控助手

多云文档监控与对比工具，支持阿里云、腾讯云、百度云、火山云文档抓取，自动检测变化并生成 AI 摘要，可作为 MCP Server 提供工具调用。

## 核心能力

- 多云文档抓取：阿里云、腾讯云、百度云、火山云
- 版本与变更检测：新增、修改、删除，支持 Diff 摘要
- AI 分析：基于通义千问（文本/可选多模态）生成变更总结
- 通知分发：aiflow、如流机器人、Webhook、本地文件
- MCP 集成：内置文档查询、变更监控、跨云对比、统计工具
- 定时任务：内置 cron 调度（`python -m src.main`）

## 运行模式

### 1. 定时/一次性扫描模式

入口：`python -m src.main`

- `--check-now`：立即执行一次扫描
- `--cron "0 9 * * *"`：覆盖配置文件中的 cron
- `--products "/vpc,/dns"`：覆盖旧版 `monitor_products`（阿里云兼容模式）

### 2. MCP Server 模式

入口：`python -m src.mcp_server`

- 默认：STDIO 传输（适合 MCP 客户端本地进程接入）
- `--http`：Streamable HTTP 模式，监听 `0.0.0.0:8080`

## 环境准备

### Python 版本

- `>=3.10`

### 安装依赖

如果你只运行 MCP 基础能力：

```bash
pip install -r requirements.txt
```

如果你要运行 `src.main`（定时扫描）或火山云并发抓取，建议补齐以下依赖：

```bash
pip install -r requirements.txt croniter pytz aiohttp
```

## 配置

### 1. `.env`（建议）

`src/config.py` 会自动加载项目根目录下 `.env`，并支持配置文件中的 `${VAR}` / `${VAR:default}` 变量替换。

示例：

```bash
# AI（建议配置；智能搜索/摘要/对比功能依赖）
DASHSCOPE_API_KEY=your_dashscope_api_key
LLM_MODEL=qwen3-coder-plus
LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1

# 通知渠道（按需）
AIFLOW_WEBHOOK_URL=https://your-aiflow-webhook
RULIU_WEBHOOK_URL=https://your-ruliu-webhook

# 兼容模式下监控产品（可选，覆盖 monitor_products）
MONITOR_PRODUCTS=/vpc,/dns,/vpn
```

### 2. `config.yaml`

关键字段：

- `crawler`：请求间隔、超时、重试
- `scheduler`：定时表达式与时区
- `monitor_clouds`：四个云厂商的启用状态与产品列表
- `llm`：模型、API Key、多模态开关
- `notifications`：通知渠道配置
- `storage.database`：SQLite 路径（默认 `./data/aliyun_docs.db`）

`monitor_clouds` 中不同云厂商的产品标识格式建议：

- `aliyun.products`：`/vpc`、`/ecs` 这类 alias
- `tencent.products`：产品名称（如 `私有网络`、`云联网`）
- `baidu.products`：产品代号（如 `VPC`、`BCC`）
- `volcano.products`：产品名称（如 `私有网络`、`云企业网`）

## 快速开始

### 一次执行扫描

```bash
python -m src.main --check-now
```

### 启动定时服务

```bash
python -m src.main
```

### 启动 MCP Server

```bash
# STDIO
python -m src.mcp_server

# HTTP (http://localhost:8080)
python -m src.mcp_server --http
```

## MCP 客户端接入示例

```json
{
  "mcpServers": {
    "cloud-doc-monitor": {
      "command": "/path/to/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/cloud-doc-monitor",
      "env": {
        "DASHSCOPE_API_KEY": "your_key"
      }
    }
  }
}
```

## MCP 工具清单

### 文档查询类（`mcp_tools_docs.py`）

- `get_doc`
- `list_product_docs`
- `smart_search_aliyun_docs`
- `list_tencent_product_docs`
- `smart_search_tencent_docs`
- `get_tencent_doc`
- `smart_search_baidu_docs`
- `get_baidu_doc`
- `smart_search_volcano_docs`
- `get_volcano_doc`

### 变更检测类（`mcp_tools_changes.py`）

- `check_doc_changes`
- `check_product_changes`
- `monitor_products`
- `check_recent_updates`

### 跨云对比类（`mcp_tools_compare.py`）

- `compare_cloud_docs`（阿里云 vs 百度云）
- `compare_tencent_baidu_docs`（腾讯云 vs 百度云）
- `compare_volcano_baidu_docs`（火山云 vs 百度云）

### 辅助类（`mcp_tools_misc.py`）

- `summarize_doc_diff`
- `get_statistics`

## MCP 调用示例

```text
# 阿里云：获取单篇文档
get_doc(url="/vpc/user-guide/what-is-vpc")

# 腾讯云：通过 URL 获取文档
get_doc(
  url="https://cloud.tencent.com/document/product/215/118431",
  cloud="tencent"
)

# 腾讯云：列目录
list_tencent_product_docs(product_id="215", limit=30)

# 阿里云：监控多个产品并发送通知
monitor_products(
  products=["/vpc", "/dns"],
  cloud="aliyun",
  send_notification=true
)

# 腾讯云：监控多个产品（此工具建议传 product_id）
monitor_products(
  products=["215", "1003"],
  cloud="tencent",
  max_pages_per_product=50
)

# 最近 7 天更新
check_recent_updates(product_alias="/vpc", days=7, cloud="aliyun")
check_recent_updates(product_alias="私有网络", days=7, cloud="tencent")
check_recent_updates(product_alias="私有网络", days=7, cloud="volcano")

# 跨云对比
compare_cloud_docs(product="VPC")
compare_tencent_baidu_docs(product="VPC", tencent_product_id="215")
compare_volcano_baidu_docs(product="VPC", volcano_product_name="私有网络")
```

## 通知渠道

`src/notifier.py` 支持以下 `notifications[].type`：

- `aiflow`
- `ruliu`
- `webhook`
- `file`

示例：

```yaml
notifications:
  - type: "aiflow"
    enabled: true
    webhook_url: "${AIFLOW_WEBHOOK_URL}"
    retry_count: 3
    notify_users:
      - "your_user"

  - type: "ruliu"
    enabled: false
    webhook_url: "${RULIU_WEBHOOK_URL}"

  - type: "file"
    enabled: true
    output_dir: "./notifications"
```

## 目录说明

```text
src/
  main.py                 # 定时任务与一次性扫描入口
  mcp_server.py           # MCP Server 入口
  mcp_services.py         # MCP 共享服务（配置/存储/摘要器）
  mcp_tools_docs.py       # 文档检索工具
  mcp_tools_changes.py    # 变更检测工具
  mcp_tools_compare.py    # 跨云对比工具
  mcp_tools_misc.py       # 辅助工具
  crawler.py              # 阿里云爬虫
  tencent_crawler.py      # 腾讯云爬虫
  baidu_crawler.py        # 百度云爬虫
  volcano_crawler.py      # 火山云爬虫
  detector.py             # 变更检测
  summarizer.py           # AI 摘要（含可选多模态）
  storage.py              # SQLite 存储
  notifier.py             # 多渠道通知
  scheduler.py            # cron 调度
```

## 常用脚本

```bash
# MCP 本地调试启动（scripts/run_mcp_dev.py）
python scripts/run_mcp_dev.py --http

# 初始化扫描（阿里云 monitor_products）
python scripts/init_scan.py

# aiflow 通知联调
python scripts/test_ruliu_notifier.py

# 示例：腾讯云 vs 百度云文档对比
python scripts/compare_eni.py
```

## 数据与日志

- 数据库：`./data/aliyun_docs.db`
- 日志：`./logs/monitor.log`
- 文件通知输出：`./notifications/`

## 已知限制

- 跨云对比当前实现为：
  - `阿里云 vs 百度云`
  - `腾讯云 vs 百度云`
  - `火山云 vs 百度云`
- 暂不提供 `阿里云 vs 腾讯云` 的直接对比工具。
- 智能搜索与摘要依赖 LLM，可受 API Key、配额和网络环境影响。

## 许可证

MIT License

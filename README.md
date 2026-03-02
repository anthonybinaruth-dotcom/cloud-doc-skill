# 云文档监控助手

自动监控云厂商（阿里云、百度云）官网文档更新，检测变更并使用AI生成摘要通知。

## 功能特性

- 🕷️ **多云支持**: 支持阿里云、百度云文档爬取
- ?? **变更检测**: 智能识别文档的新增、修改和删除
- 🤖 **AI摘要**: 使用通义千问生成中文变更摘要
- 📢 **多渠道通知**: 支持 aiflow、如流机器人、文件输出
- 💾 **历史记录**: 完整的文档版本历史和变更记录
- 🔧 **MCP集成**: 可作为MCP服务器，供AI助手调用
- ⏰ **定时执行**: 支持 crontab 定时自动检查

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
# 通义千问 API Key（必需）
DASHSCOPE_API_KEY=your_api_key

# aiflow Webhook 地址（可选）
AIFLOW_WEBHOOK_URL=https://aiflow.baidu-int.com/server/webhook/xxx

# 监控产品列表，逗号分隔（可选，覆盖 config.yaml）
MONITOR_PRODUCTS=/vpc,/dns,/vpn
```

### 3. 运行

```bash
# 手动执行一次检查
python -m src.main --check-now

# 启动定时任务服务
python -m src.main
```

## 项目结构

```
cloud-doc-monitor/
├── src/
│   ├── main.py            # 主入口（定时任务）
│   ├── mcp_server.py      # MCP 服务器
│   ├── crawler.py         # 阿里云文档爬虫
│   ├── baidu_crawler.py   # 百度云文档爬虫
│   ├── detector.py        # 变更检测
│   ├── summarizer.py      # AI 摘要生成
│   ├── notifier.py        # 通知发送
│   ├── storage.py         # 数据存储
│   ├── scheduler.py       # 任务调度
│   ├── config.py          # 配置管理
│   ├── models.py          # 数据模型
│   └── utils.py           # 工具函数
├── bin/
│   └── run_check.sh       # 定时任务脚本
├── config.yaml            # 配置文件
├── requirements.txt       # Python 依赖
└── .env                   # 环境变量（需自行创建）
```

## 配置说明

编辑 `config.yaml`：

```yaml
# 监控产品列表（阿里云文档 alias）
monitor_products:
  - "/vpc"      # 专有网络
  - "/dns"      # 云解析
  - "/vpn"      # VPN网关

# 调度配置
scheduler:
  cron: "15 11 * * *"  # 每天 11:15 执行
  timezone: "Asia/Shanghai"

# 通知配置
notifications:
  - type: "aiflow"
    enabled: true
    webhook_url: "${AIFLOW_WEBHOOK_URL}"
  - type: "file"
    enabled: true
    output_dir: "./notifications"
```

## MCP 集成

### 启动 MCP 服务器

```bash
python -m src.mcp_server
```

### 配置 MCP 客户端

在 `.kiro/settings/mcp.json` 中：

```json
{
  "mcpServers": {
    "cloud-doc-monitor": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "cwd": "/path/to/project",
      "env": {
        "DASHSCOPE_API_KEY": "your_key",
        "AIFLOW_WEBHOOK_URL": "your_webhook_url",
        "MONITOR_PRODUCTS": "/vpc,/dns,/vpn"
      }
    }
  }
}
```

### 可用 MCP 工具

| 工具名 | 功能 |
|--------|------|
| `get_doc` | 获取单篇文档内容（支持关键词搜索） |
| `list_product_docs` | 列出产品下所有文档 |
| `check_doc_changes` | 检查指定文档的变更 |
| `check_product_changes` | 检查单个产品的文档变更 |
| `monitor_products` | **主入口** - 监控多个产品并发送通知 |
| `check_recent_updates` | 检查最近N天的更新 |
| `compare_cloud_docs` | 对比阿里云和百度云的产品文档 |
| `get_statistics` | 获取监控统计信息 |

### 调用示例

```
// 监控多个产品
monitor_products(products=["/vpc", "/dns"], send_notification=true)

// 检查最近7天更新
check_recent_updates(product_alias="/vpc", days=7)

// 对比阿里云和百度云
compare_cloud_docs(product="VPC")
```

## 定时任务配置

### 方式一：系统 crontab

```bash
# 添加定时任务（每天 11:15 执行）
crontab -e

# 添加以下行
15 11 * * * /path/to/project/bin/run_check.sh >> /path/to/project/logs/cron.log 2>&1
```

### 方式二：Docker

```bash
docker-compose up -d
```

## 通知渠道

### aiflow（百度内部）

```yaml
notifications:
  - type: "aiflow"
    webhook_url: "${AIFLOW_WEBHOOK_URL}"
```

通知数据格式：
```json
{
  "event": "doc_change_notification",
  "user": "alimujiangayiziba",
  "title": "云文档监控报告",
  "summary": "...",
  "changes": [...]
}
```

### 如流机器人

```yaml
notifications:
  - type: "ruliu"
    webhook_url: "${RULIU_WEBHOOK_URL}"
```

### 本地文件

```yaml
notifications:
  - type: "file"
    output_dir: "./notifications"
```

## 开发

```bash
# 语法检查
python -m py_compile src/*.py

# 运行测试
pytest

# 测试通知
python scripts/test_ruliu_notifier.py
```

## 许可证

MIT License
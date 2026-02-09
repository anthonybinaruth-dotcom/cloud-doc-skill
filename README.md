# 阿里云文档监控助手

自动监控阿里云官网文档更新，并使用AI生成变更摘要通知。

## 功能特性

- 🕷️ **自动爬取**: 定期爬取阿里云官网公开文档
- 🔍 **变更检测**: 智能识别文档的新增、修改和删除
- 🤖 **AI摘要**: 使用大模型生成简洁的中文变更摘要
- 📢 **多渠道通知**: 支持Webhook、文件输出等通知方式
- 💾 **历史记录**: 完整的文档版本历史和变更记录
- 🔧 **MCP集成**: 可作为MCP服务器运行，提供工具接口

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置

1. 复制配置模板：
```bash
cp config.yaml.example config.yaml
```

2. 编辑 `config.yaml`，配置必要参数：
   - 大模型API密钥
   - 通知渠道（Webhook URL等）
   - 爬虫参数

3. 设置环境变量：
```bash
export HUGGINGFACE_API_KEY="your_api_key"
export WEBHOOK_URL="your_webhook_url"
```

### 初始化数据库

```bash
python -m src.init_db
```

### 运行

```bash
# 启动监控服务（定时任务）
python -m src.main

# 手动触发一次检查
python -m src.main --check-now
```

## 项目结构

```
aliyun-doc-monitor/
├── src/                    # 源代码
│   ├── main.py            # 主入口
│   ├── crawler.py         # 爬虫模块
│   ├── detector.py        # 变更检测
│   ├── summarizer.py      # AI摘要
│   ├── notifier.py        # 通知发送
│   ├── storage.py         # 数据存储
│   ├── scheduler.py       # 任务调度
│   ├── mcp_server.py      # MCP服务器
│   ├── config.py          # 配置管理
│   ├── models.py          # 数据模型
│   └── utils.py           # 工具函数
├── tests/                 # 测试文件
├── data/                  # 数据库文件
├── logs/                  # 日志文件
├── notifications/         # 通知输出
├── config.yaml            # 配置文件
└── requirements.txt       # 依赖列表
```

## 配置说明

详见 `config.yaml` 文件中的注释。

主要配置项：
- `crawler`: 爬虫参数（请求延迟、重试次数等）
- `scheduler`: 调度参数（执行频率、时区等）
- `llm`: 大模型配置（提供商、模型、API密钥等）
- `notifications`: 通知渠道配置
- `storage`: 数据存储配置

## MCP集成

作为MCP服务器运行：

```bash
python -m src.mcp_server
```

在 Kiro 的 `mcp.json` 中配置：

```json
{
  "mcpServers": {
    "aliyun-doc-monitor": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "env": {
        "HUGGINGFACE_API_KEY": "your_key"
      }
    }
  }
}
```

可用工具：
- `trigger_check`: 手动触发文档检查
- `get_recent_changes`: 获取最近的文档变更
- `get_scan_history`: 获取扫描历史记录
- `configure_monitor`: 配置监控参数
- `get_statistics`: 获取统计信息

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_crawler.py

# 运行Property-Based Tests
pytest tests/test_properties.py

# 生成覆盖率报告
pytest --cov=src --cov-report=html
```

### 代码格式化

```bash
black src/ tests/
```

### 类型检查

```bash
mypy src/
```

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！

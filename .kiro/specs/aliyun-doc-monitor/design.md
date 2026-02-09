# 阿里云文档监控助手 - 设计文档

## 1. 系统架构

### 1.1 整体架构

系统采用模块化设计，分为以下核心组件：

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Server (可选)                     │
│                  提供工具接口和配置管理                    │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                      调度器 (Scheduler)                  │
│                   定时触发检查任务                        │
└─────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼────────┐  ┌──────▼──────┐  ┌────────▼────────┐
│  文档爬虫模块   │  │  变更检测    │  │   AI摘要生成    │
│   (Crawler)    │─▶│  (Detector)  │─▶│  (Summarizer)   │
└────────────────┘  └──────────────┘  └─────────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                ┌───────────▼───────────┐
                │    通知发送模块        │
                │    (Notifier)         │
                └───────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼────────┐  ┌──────▼──────┐  ┌────────▼────────┐
│   数据存储      │  │   配置管理   │  │    日志系统     │
│   (Storage)    │  │   (Config)   │  │    (Logger)     │
└────────────────┘  └──────────────┘  └─────────────────┘
```

### 1.2 技术栈选择

- **编程语言**: Python 3.10+
- **Web爬虫**: BeautifulSoup4 + requests / Scrapy
- **数据存储**: SQLite (初期) / PostgreSQL (扩展)
- **任务调度**: APScheduler
- **大模型集成**: LangChain / 直接API调用
- **MCP框架**: FastMCP
- **配置管理**: YAML / JSON
- **日志**: Python logging

## 2. 核心模块设计

### 2.1 文档爬虫模块 (Crawler)

#### 职责
- 爬取阿里云文档网站
- 解析HTML内容
- 提取文档元数据和正文

#### 接口设计
```python
class DocumentCrawler:
    def crawl_site(self, base_url: str) -> List[Document]:
        """爬取整个文档站点"""
        pass
    
    def crawl_page(self, url: str) -> Document:
        """爬取单个文档页面"""
        pass
    
    def extract_links(self, html: str, base_url: str) -> List[str]:
        """提取页面中的文档链接"""
        pass
    
    def parse_document(self, html: str, url: str) -> Document:
        """解析文档内容"""
        pass
```

#### 数据模型
```python
@dataclass
class Document:
    url: str
    title: str
    content: str
    content_hash: str
    last_modified: Optional[datetime]
    crawled_at: datetime
    metadata: Dict[str, Any]
```

#### 实现要点
- 使用requests-html或Scrapy进行爬取
- 实现URL去重和已访问URL跟踪
- 遵守robots.txt规则
- 实现请求频率限制（1秒/请求）
- 处理分页和动态加载内容
- 实现重试机制（最多3次）

### 2.2 变更检测模块 (Detector)

#### 职责
- 对比文档内容变化
- 识别新增、修改、删除的文档
- 生成差异报告

#### 接口设计
```python
class ChangeDetector:
    def detect_changes(
        self, 
        old_docs: List[Document], 
        new_docs: List[Document]
    ) -> ChangeReport:
        """检测文档变更"""
        pass
    
    def compute_diff(self, old_content: str, new_content: str) -> str:
        """计算内容差异"""
        pass
    
    def categorize_change(self, diff: str) -> ChangeType:
        """分类变更类型"""
        pass
```

#### 数据模型
```python
@dataclass
class ChangeReport:
    added: List[Document]
    modified: List[DocumentChange]
    deleted: List[Document]
    timestamp: datetime

@dataclass
class DocumentChange:
    document: Document
    old_content_hash: str
    new_content_hash: str
    diff: str
    change_type: ChangeType

class ChangeType(Enum):
    MINOR = "minor"  # 小改动
    MAJOR = "major"  # 大改动
    STRUCTURAL = "structural"  # 结构性变化
```

#### 实现要点
- 使用内容哈希（SHA256）快速判断是否变更
- 使用difflib生成详细差异
- 实现智能变更分类（基于变更行数和关键词）
- 过滤无意义的变更（如时间戳、版权信息）

### 2.3 AI摘要生成模块 (Summarizer)

#### 职责
- 调用大模型API生成摘要
- 处理长文本分段
- 格式化摘要输出

#### 接口设计
```python
class AISummarizer:
    def summarize_change(self, change: DocumentChange) -> str:
        """为单个文档变更生成摘要"""
        pass
    
    def summarize_batch(self, changes: List[DocumentChange]) -> str:
        """为批量变更生成总体摘要"""
        pass
    
    def chunk_content(self, content: str, max_tokens: int) -> List[str]:
        """分割长内容"""
        pass
```

#### 大模型适配器设计
```python
class LLMAdapter(ABC):
    @abstractmethod
    def generate(self, prompt: str, max_tokens: int) -> str:
        pass

class HuggingFaceAdapter(LLMAdapter):
    """HuggingFace Inference API适配器"""
    pass

class OllamaAdapter(LLMAdapter):
    """Ollama本地模型适配器"""
    pass

class OpenAIAdapter(LLMAdapter):
    """OpenAI API适配器（备用）"""
    pass
```

#### Prompt设计
```
系统提示词：
你是一个技术文档分析助手。你的任务是分析文档变更内容，生成简洁的中文摘要。

用户提示词模板：
以下是阿里云文档《{title}》的变更内容：

{diff}

请生成一个200-500字的中文摘要，包含：
1. 变更类型（新增/修改/删除）
2. 主要变更点（3-5条）
3. 可能的影响范围
4. 建议的后续行动（如有）

摘要应该：
- 使用简洁的技术语言
- 突出重要信息
- 避免冗余描述
```

#### 实现要点
- 优先使用HuggingFace免费API（如Qwen、Llama等）
- 实现token计数和内容截断
- 处理API限流和错误
- 缓存生成的摘要

### 2.4 通知发送模块 (Notifier)

#### 职责
- 发送变更通知
- 支持多种通知渠道
- 处理发送失败和重试

#### 接口设计
```python
class Notifier(ABC):
    @abstractmethod
    def send(self, notification: Notification) -> bool:
        pass

class WebhookNotifier(Notifier):
    """Webhook通知"""
    pass

class FileNotifier(Notifier):
    """文件输出通知"""
    pass

class EmailNotifier(Notifier):
    """邮件通知（未来扩展）"""
    pass
```

#### 数据模型
```python
@dataclass
class Notification:
    title: str
    summary: str
    changes: List[DocumentChange]
    timestamp: datetime
    metadata: Dict[str, Any]
```

#### 通知格式（Webhook JSON）
```json
{
  "title": "阿里云文档更新通知",
  "timestamp": "2026-02-06T10:00:00Z",
  "summary": "本周检测到5个文档更新",
  "changes": [
    {
      "document_title": "ECS实例规格族",
      "document_url": "https://help.aliyun.com/...",
      "change_type": "modified",
      "summary": "新增了第7代实例规格...",
      "detected_at": "2026-02-06T10:00:00Z"
    }
  ]
}
```

#### 实现要点
- 实现重试机制（指数退避）
- 支持批量通知和单条通知
- 记录发送历史
- 支持通知模板自定义

### 2.5 数据存储模块 (Storage)

#### 职责
- 持久化文档数据
- 存储历史版本
- 提供查询接口

#### 数据库设计
```sql
-- 文档表
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    last_modified TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档内容历史表
CREATE TABLE document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

-- 检查记录表
CREATE TABLE scan_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT NOT NULL,
    documents_scanned INTEGER,
    changes_detected INTEGER,
    error_message TEXT
);

-- 变更记录表
CREATE TABLE changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    diff TEXT,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (scan_id) REFERENCES scan_records(id),
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

-- 通知记录表
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TIMESTAMP,
    error_message TEXT,
    FOREIGN KEY (scan_id) REFERENCES scan_records(id)
);
```

#### 接口设计
```python
class DocumentStorage:
    def save_document(self, doc: Document) -> int:
        """保存文档"""
        pass
    
    def get_document(self, url: str) -> Optional[Document]:
        """获取文档"""
        pass
    
    def get_all_documents(self) -> List[Document]:
        """获取所有文档"""
        pass
    
    def save_version(self, doc_id: int, content: str) -> int:
        """保存文档版本"""
        pass
    
    def get_latest_version(self, doc_id: int) -> Optional[str]:
        """获取最新版本内容"""
        pass
    
    def save_scan_record(self, record: ScanRecord) -> int:
        """保存扫描记录"""
        pass
    
    def save_change(self, change: Change) -> int:
        """保存变更记录"""
        pass
```

### 2.6 调度器模块 (Scheduler)

#### 职责
- 定时触发文档检查任务
- 管理任务执行状态
- 处理任务失败和重试

#### 接口设计
```python
class DocumentMonitorScheduler:
    def start(self):
        """启动调度器"""
        pass
    
    def stop(self):
        """停止调度器"""
        pass
    
    def run_check_now(self):
        """立即执行检查"""
        pass
    
    def get_next_run_time(self) -> datetime:
        """获取下次执行时间"""
        pass
```

#### 实现要点
- 使用APScheduler的CronTrigger（每周一次）
- 实现任务执行日志
- 处理长时间运行的任务
- 支持手动触发

### 2.7 MCP服务器模块 (可选)

#### 职责
- 提供MCP工具接口
- 暴露配置和查询功能
- 集成所有核心模块

#### MCP工具定义
```python
from fastmcp import FastMCP

mcp = FastMCP("aliyun-doc-monitor")

@mcp.tool()
def trigger_check() -> str:
    """手动触发文档检查"""
    pass

@mcp.tool()
def get_recent_changes(days: int = 7) -> List[Dict]:
    """获取最近的文档变更"""
    pass

@mcp.tool()
def get_scan_history(limit: int = 10) -> List[Dict]:
    """获取扫描历史记录"""
    pass

@mcp.tool()
def configure_monitor(config: Dict[str, Any]) -> str:
    """配置监控参数"""
    pass

@mcp.tool()
def get_statistics() -> Dict[str, Any]:
    """获取统计信息"""
    pass
```

## 3. 配置管理

### 3.1 配置文件结构 (config.yaml)
```yaml
# 爬虫配置
crawler:
  base_url: "https://help.aliyun.com"
  request_delay: 1.0  # 秒
  max_retries: 3
  timeout: 30
  user_agent: "AliyunDocMonitor/1.0"
  
# 调度配置
scheduler:
  enabled: true
  cron: "0 9 * * 1"  # 每周一上午9点
  timezone: "Asia/Shanghai"

# 大模型配置
llm:
  provider: "huggingface"  # huggingface, ollama, openai
  model: "Qwen/Qwen2.5-7B-Instruct"
  api_key: "${HUGGINGFACE_API_KEY}"
  api_base: "https://api-inference.huggingface.co/models"
  max_tokens: 1000
  temperature: 0.3

# 通知配置
notifications:
  - type: "webhook"
    enabled: true
    url: "${WEBHOOK_URL}"
    retry_count: 3
  - type: "file"
    enabled: true
    output_dir: "./notifications"

# 存储配置
storage:
  type: "sqlite"
  database: "./data/aliyun_docs.db"
  keep_versions: 10  # 保留最近10个版本

# 日志配置
logging:
  level: "INFO"
  file: "./logs/monitor.log"
  max_size: "10MB"
  backup_count: 5

# MCP配置
mcp:
  enabled: true
  host: "localhost"
  port: 8000
```

## 4. 工作流程

### 4.1 完整检查流程
```
1. 调度器触发检查任务
   ↓
2. 爬虫模块爬取所有文档
   ↓
3. 存储模块保存新文档数据
   ↓
4. 变更检测模块对比差异
   ↓
5. AI摘要模块生成变更摘要
   ↓
6. 通知模块发送通知
   ↓
7. 记录检查结果和统计信息
```

### 4.2 错误处理流程
```
任务执行失败
   ↓
记录错误日志
   ↓
判断是否可重试
   ↓
是 → 等待后重试（最多3次）
否 → 发送错误通知
```

## 5. 正确性属性 (Property-Based Testing)

### 5.1 爬虫模块属性

**属性 5.1.1**: URL去重正确性
- **描述**: 对于任意URL列表，去重后不应包含重复URL
- **形式化**: ∀ urls: List[str], len(deduplicate(urls)) == len(set(urls))
- **测试策略**: 生成包含重复URL的列表，验证去重结果

**属性 5.1.2**: 内容哈希一致性
- **描述**: 相同内容应产生相同哈希，不同内容应产生不同哈希
- **形式化**: ∀ content1, content2: str, (content1 == content2) ⟺ (hash(content1) == hash(content2))
- **测试策略**: 生成随机内容，验证哈希函数的确定性和唯一性

### 5.2 变更检测模块属性

**属性 5.2.1**: 变更检测完整性
- **描述**: 所有文档都应被分类为新增、修改、删除或未变更之一
- **形式化**: ∀ old_docs, new_docs, report = detect_changes(old_docs, new_docs),
  len(old_docs) + len(new_docs) == len(report.added) + len(report.modified) + len(report.deleted) + len(unchanged)
- **测试策略**: 生成不同的文档集合，验证分类完整性

**属性 5.2.2**: 哈希相同则内容未变更
- **描述**: 如果文档哈希相同，则不应被标记为已修改
- **形式化**: ∀ doc1, doc2, doc1.hash == doc2.hash → doc2 ∉ modified_docs
- **测试策略**: 生成哈希相同的文档对，验证不会被误判为修改

### 5.3 存储模块属性

**属性 5.3.1**: 保存后可检索
- **描述**: 保存的文档应该能够通过URL检索到
- **形式化**: ∀ doc: Document, save_document(doc) → get_document(doc.url) == doc
- **测试策略**: 生成随机文档，保存后立即检索验证

**属性 5.3.2**: 版本历史单调递增
- **描述**: 文档版本号应该单调递增
- **形式化**: ∀ doc_id, versions = get_versions(doc_id), 
  ∀ i < j, versions[i].version < versions[j].version
- **测试策略**: 保存多个版本，验证版本号顺序

### 5.4 通知模块属性

**属性 5.4.1**: 重试幂等性
- **描述**: 多次发送相同通知应该是幂等的（不产生副作用）
- **形式化**: ∀ notification, send(notification) == send(send(notification))
- **测试策略**: 使用mock验证重复发送不会产生多次副作用

## 6. 测试框架

### 6.1 单元测试
- 使用pytest作为测试框架
- 每个模块都有对应的测试文件
- 测试覆盖率目标：>80%

### 6.2 Property-Based Testing
- 使用Hypothesis库
- 为每个正确性属性编写property test
- 生成策略：
  - URL: 使用st.text()生成合法URL
  - 文档内容: 使用st.text()生成随机文本
  - 时间戳: 使用st.datetimes()生成时间

### 6.3 集成测试
- 测试完整的检查流程
- 使用mock模拟外部依赖（网络请求、API调用）
- 验证模块间交互

## 7. 部署方案

### 7.1 本地部署
```bash
# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python -m aliyun_doc_monitor.init_db

# 配置环境变量
export HUGGINGFACE_API_KEY="your_key"
export WEBHOOK_URL="your_webhook_url"

# 启动服务
python -m aliyun_doc_monitor.main
```

### 7.2 Docker部署
```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["python", "-m", "aliyun_doc_monitor.main"]
```

### 7.3 MCP服务器部署
```json
{
  "mcpServers": {
    "aliyun-doc-monitor": {
      "command": "python",
      "args": ["-m", "aliyun_doc_monitor.mcp_server"],
      "env": {
        "HUGGINGFACE_API_KEY": "your_key"
      }
    }
  }
}
```

## 8. 项目结构
```
aliyun-doc-monitor/
├── src/
│   ├── __init__.py
│   ├── main.py                 # 主入口
│   ├── crawler.py              # 爬虫模块
│   ├── detector.py             # 变更检测
│   ├── summarizer.py           # AI摘要
│   ├── notifier.py             # 通知发送
│   ├── storage.py              # 数据存储
│   ├── scheduler.py            # 任务调度
│   ├── mcp_server.py           # MCP服务器
│   ├── config.py               # 配置管理
│   ├── models.py               # 数据模型
│   └── utils.py                # 工具函数
├── tests/
│   ├── test_crawler.py
│   ├── test_detector.py
│   ├── test_summarizer.py
│   ├── test_storage.py
│   ├── test_properties.py      # Property-based tests
│   └── test_integration.py
├── config.yaml                 # 配置文件
├── requirements.txt            # 依赖列表
├── Dockerfile
├── README.md
└── .gitignore
```

## 9. 依赖清单 (requirements.txt)
```
# Web爬虫
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0

# 任务调度
apscheduler>=3.10.0

# 数据库
sqlalchemy>=2.0.0

# 大模型集成
langchain>=0.1.0
openai>=1.0.0  # 可选

# MCP框架
fastmcp>=0.1.0

# 配置管理
pyyaml>=6.0.0
python-dotenv>=1.0.0

# 测试
pytest>=7.4.0
hypothesis>=6.92.0
pytest-cov>=4.1.0

# 工具
python-dateutil>=2.8.0
```

## 10. 开发里程碑

### 里程碑1: 基础框架 (1-2周)
- [ ] 项目结构搭建
- [ ] 配置管理实现
- [ ] 数据模型定义
- [ ] 数据库设计和初始化

### 里程碑2: 爬虫和存储 (2-3周)
- [ ] 爬虫模块实现
- [ ] 存储模块实现
- [ ] 单元测试
- [ ] Property-based tests

### 里程碑3: 变更检测和AI摘要 (2-3周)
- [ ] 变更检测实现
- [ ] 大模型适配器实现
- [ ] AI摘要生成
- [ ] 测试和优化

### 里程碑4: 通知和调度 (1-2周)
- [ ] 通知模块实现
- [ ] 调度器实现
- [ ] 集成测试
- [ ] 端到端测试

### 里程碑5: MCP集成 (1周)
- [ ] MCP服务器实现
- [ ] 工具接口定义
- [ ] 文档编写
- [ ] 部署测试

## 11. 风险缓解

### 11.1 网站结构变化
- **风险**: 阿里云文档网站改版导致爬虫失效
- **缓解**: 
  - 使用灵活的CSS选择器和XPath
  - 实现多种解析策略
  - 添加网站结构变化检测
  - 定期人工验证

### 11.2 API限流
- **风险**: 大模型API调用受限
- **缓解**:
  - 实现多个LLM后端
  - 添加本地模型支持（Ollama）
  - 实现请求队列和限流
  - 缓存摘要结果

### 11.3 性能问题
- **风险**: 大规模文档处理耗时过长
- **缓解**:
  - 实现增量爬取
  - 使用多线程/异步IO
  - 优化数据库查询
  - 添加进度监控

## 12. 未来扩展

### 12.1 功能扩展
- 支持更多文档网站（AWS、Azure等）
- 添加文档搜索功能
- 实现文档变更趋势分析
- 添加Web UI管理界面

### 12.2 技术优化
- 使用分布式爬虫（Scrapy-Redis）
- 实现向量数据库存储（用于语义搜索）
- 添加GraphQL API
- 支持实时推送（WebSocket）

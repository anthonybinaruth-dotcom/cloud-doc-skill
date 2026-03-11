# 异步 Skill 使用指南

## 问题背景

当 OpenClaw 或其他调用方调用耗时的 skill（如 `compare_docs`、`fetch_doc` 大量文档）时，可能会遇到超时问题。

## 解决方案

我们提供了异步版本的 skill，采用"提交任务 → 轮询状态 → 获取结果"的模式。

## 使用方式

### 1. 调用异步 skill（立即返回）

```python
# 使用异步版本的 compare_docs
result = assistant.compare_docs_async(
    left={"cloud": "baidu", "product": "对等连接"},
    right={"cloud": "tencent", "product": "对等连接"}
)

# 立即返回 task_id
print(result)
# {
#     "task_id": "550e8400-e29b-41d4-a716-446655440000",
#     "status": "pending",
#     "message": "任务已提交，请使用 get_task_status 查询进度"
# }
```

### 2. 查询任务状态

```python
task_id = result["task_id"]

# 查询任务状态
status = assistant.get_task_status(task_id=task_id)
print(status)
# {
#     "task_id": "550e8400-e29b-41d4-a716-446655440000",
#     "status": "running",  # pending | running | completed | failed
#     "created_at": "2024-01-01T00:00:00",
#     "started_at": "2024-01-01T00:00:01",
#     "completed_at": null,
#     "result": null,
#     "error": null
# }
```

### 3. 获取任务结果

```python
# 方式 1：非阻塞获取（如果未完成返回状态）
result = assistant.get_task_result(task_id=task_id)

# 方式 2：阻塞等待直到完成（最多等待 300 秒）
result = assistant.get_task_result(task_id=task_id, wait=True, timeout=300)

# 如果任务完成，返回实际结果
print(result)
# {
#     "machine": {...},
#     "human": {...}
# }
```

## 可用的异步 Skill

| 同步版本 | 异步版本 | 说明 |
|---------|---------|------|
| `fetch_doc` | `fetch_doc_async` | 抓取文档 |
| `compare_docs` | `compare_docs_async` | 对比文档 |
| `check_changes` | `check_changes_async` | 检查变更 |
| `run_monitor` | `run_monitor_async` | 运行监控 |

## OpenClaw 调用示例

### 场景 1：快速任务（使用同步版本）

```python
# 抓取单篇文档（通常很快）
result = skill_call("fetch_doc", {
    "cloud": "aliyun",
    "doc_ref": "/vpc/product-overview/what-is-vpc"
})
```

### 场景 2：耗时任务（使用异步版本）

```python
# 第一步：提交任务
task_info = skill_call("compare_docs_async", {
    "left": {"cloud": "baidu", "product": "对等连接"},
    "right": {"cloud": "tencent", "product": "对等连接"}
})

task_id = task_info["task_id"]

# 第二步：轮询状态（每 5 秒查询一次）
import time
while True:
    status = skill_call("get_task_status", {"task_id": task_id})
    
    if status["status"] == "completed":
        # 任务完成，获取结果
        result = skill_call("get_task_result", {"task_id": task_id})
        break
    elif status["status"] == "failed":
        # 任务失败
        print(f"任务失败: {status['error']}")
        break
    else:
        # 继续等待
        print(f"任务状态: {status['status']}")
        time.sleep(5)
```

## 性能优化建议

### 1. 并发抓取（已实现）
- 使用 `ThreadPoolExecutor` 并发抓取多个文档
- 默认最多 10 个并发线程

### 2. 缓存机制（已实现）
- 文档内容缓存 24 小时
- 避免重复抓取相同文档

### 3. 降低延迟（已优化）
- `request_delay`: 1.0s → 0.3s
- `max_retries`: 3 → 2
- `timeout`: 30s → 15s

### 4. LLM 优化（已优化）
- `max_tokens`: 1000 → 500（摘要更简洁）
- `temperature`: 0.3 → 0.1（生成更快）

## 预期性能提升

| 场景 | 优化前 | 优化后 | 提升 |
|-----|-------|-------|------|
| 抓取 20 篇文档 | ~40s | ~8s | 5x |
| 对比 2 个产品 | ~60s | ~15s | 4x |
| 生成摘要 | ~3s/篇 | ~1.5s/篇 | 2x |

## 注意事项

1. **任务过期**：任务结果默认保留 7 天，过期后自动清理
2. **并发限制**：建议不要同时提交超过 10 个任务
3. **超时设置**：OpenClaw 调用时建议设置 60s 超时（异步版本立即返回）
4. **错误处理**：任务失败时会在 `error` 字段返回错误信息

## 故障排查

### 问题：任务一直处于 pending 状态
- 检查后台线程是否正常启动
- 查看日志文件 `logs/monitor.log`

### 问题：任务失败
- 查看 `get_task_status` 返回的 `error` 字段
- 检查 LLM API Key 是否正确
- 检查网络连接是否正常

### 问题：找不到任务
- 任务可能已过期（默认 7 天）
- 检查 task_id 是否正确

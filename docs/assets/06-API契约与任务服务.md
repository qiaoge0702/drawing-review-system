# 06-API契约与任务服务

**位置**: 
- API: `app/routers/generate.py` (~160行)
- Service: `app/services/generation_service.py` (~200行)

## 6.1 API端点（冻结：只加不改）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/generate` | 创建任务（202 Accepted） |
| GET | `/api/generate` | 任务列表 |
| GET | `/api/generate/{task_id}` | 任务详情（8步状态+产物） |
| GET | `/api/generate/{task_id}/steps/{step}/snapshot` | 真图快照（PNG） |
| POST | `/api/generate/{task_id}/rerun` | 从指定步骤重跑 |
| GET | `/api/generate/{task_id}/artifacts/{step}/{filename}` | 下载产物 |

## 6.2 TaskResult契约（向后兼容）

```python
{
    "task_id": str,
    "status": PipelineState,
    "progress": int,         # 0-100
    "current_step": int,     # 0-8
    "steps": [StepResult],
    # 新增字段（只加不改）: snapshot_available, snapshot_url
}
```

## 6.3 产物安全（纵深防御）

```python
file_path = Path(artifact.path).resolve()
step_dir = (settings.storage.temp_dir / "generate" / task_id / f"step_{step}").resolve()
if not file_path.is_relative_to(step_dir):
    raise HTTPException(status_code=403, detail="非法产物路径")
```

## 6.4 generation_service核心方法

| 方法 | 说明 |
|------|------|
| `create_task(source_file, config)` | 创建任务，返回task_id，排队执行 |
| `rerun_task(task_id, from_step, overrides)` | 校验范围1-8，非运行状态才允许 |
| `get_task(task_id)` | 内存优先，磁盘result.json回退 |
| `get_step_snapshot_path(task_id, step)` | 按候选路径探测preview.png |

## 6.5 并发约束

```python
# 全局串行执行（SW COM单线程限制）
_queue: asyncio.Queue  # 任务队列
_worker_task: asyncio.Task  # 单工作器
```

## 6.6 WS进度推送

```python
_notify: Callable[[str, Dict], Awaitable[None]]  # 由main.py注入
# 消息类型: {"type": "step_start"|"step"|"finished", ...}
```

## 6.7 纪律与红线

- **契约冻结**: 不修改现有字段语义，不删除端点
- **向后兼容**: 新增字段默认None/有默认值，旧客户端无感
- **task_id校验**: 磁盘回退前校验格式 `[a-zA-Z0-9_-]{1,64}`，防路径逃逸

## 6.8 方案B命运
**原样复用**。端点与模型保持稳定，Step3/7产物路径约定不变。

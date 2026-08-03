# 02-sw_com单线程执行器

**位置**: `app/generators/sw_com.py` (~60行)

## 2.1 职责
解决两个骨架审查阻断问题：
- **B2**: 同步COM调用直接跑在async函数里会阻塞事件循环
- **B3**: COM线程亲和性（pythoncom要求每个线程独立CoInitialize）

## 2.2 关键接口与契约

```python
async def run_sw(func: Callable[..., T], *args, **kwargs) -> T
```

**内部实现**:
```python
# 全局单线程执行器：max_workers=1，保证COM apartment亲和
_sw_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="sw-com",
    initializer=_com_init,  # pythoncom.CoInitialize()
)
```

## 2.3 纪律与红线（COM铁律）

| 纪律 | 说明 |
|------|------|
| byref传int | OpenDoc6错误码用 `VARIANT(VT_BYREF \| VT_I4, 0)` |
| P()属性怪癖 | Position必须传 `VT_ARRAY\|VT_R8` 的VARIANT；直传list会列集错误 |
| Silent必带 | OpenDoc6必带options=1（Silent），抑制模态弹窗防挂死 |
| 禁杀进程 | 严禁强杀SW进程；文档用完 `CloseAllDocuments(True)` |

## 2.4 测试位置
- `tests/unit/test_sw_com.py`（如存在）验证超时行为

## 2.5 方案B命运
**原样复用**。所有SW COM调用必须经此执行器排队。

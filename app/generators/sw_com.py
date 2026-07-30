"""
SolidWorks COM 专用执行器

解决两个骨架审查阻断问题：
- B2: 同步 COM 调用直接跑在 async 函数里会阻塞事件循环，wait_for 超时失效
- B3: COM 线程亲和性 —— pythoncom 要求每个线程独立 CoInitialize，
      且同一 COM 对象必须在同一线程（apartment）内使用

方案：全局单线程 ThreadPoolExecutor，所有 SW COM 调用统一排队到该线程执行。
单线程保证 COM apartment 亲和，同时天然串行化 SW 操作（SW 本身不支持并发）。

注意：wait_for 超时只能放弃等待，无法强制终止已卡死的 COM 调用线程；
该线程会阻塞后续 SW 调用直至 COM 返回。这是 pywin32 的固有限制，M1 接受。
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

import pythoncom

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _com_init():
    """worker 线程初始化 COM apartment"""
    pythoncom.CoInitialize()
    logger.debug("SW COM worker thread initialized")


# 全局单线程执行器：COM 亲和 + SW 操作串行化
_sw_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="sw-com",
    initializer=_com_init,
)


async def run_sw(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    在 SW COM 专用线程中执行同步 COM 调用

    Args:
        func: 同步函数（内部可安全使用 win32com / SWParser）
        *args, **kwargs: 透传参数

    Returns:
        func 的返回值
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        func = partial(func, **kwargs)  # type: ignore[assignment]
    return await loop.run_in_executor(_sw_executor, func, *args)

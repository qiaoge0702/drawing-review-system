"""
步骤快照路由测试（方案B：真图快照预览）

覆盖：
  GET /api/generate/{task_id}/steps/{n}/snapshot  快照流式返回 / 404
  GET /api/generate/{task_id}                     steps 带出快照可用标记（只加不改）
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.generation import (
    PipelineState,
    StepName,
    StepResult,
    StepStatus,
    TaskResult,
)
from app.routers import generate as generate_router
from app.services.generation_service import GenerationService

# 1x1 红色 PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d49444154789c626000010000050001a5f645400000000049454e44ae426082"
)


@pytest.fixture
def service(tmp_path, monkeypatch) -> GenerationService:
    """注入内存任务 + tmp_path 作为流水线存储根目录的 Service"""
    svc = GenerationService()
    monkeypatch.setattr(
        GenerationService, "_task_dir", lambda self, task_id: tmp_path / task_id
    )
    task = TaskResult(
        task_id="task_abc123",
        status=PipelineState.RUNNING,
        source_file="E:/models/demo.SLDASM",
        steps=[
            StepResult(
                step=3,
                name=StepName.VIEW_PROJECT,
                status=StepStatus.COMPLETED,
                duration_ms=1234,
                output_data={"drawing_path": "E:/out/demo.SLDDRW"},
            ),
            StepResult(step=4, name=StepName.DIMENSION, status=StepStatus.RUNNING),
        ],
    )
    svc._tasks["task_abc123"] = task
    return svc


@pytest.fixture
def client(service) -> TestClient:
    app = FastAPI()
    app.include_router(generate_router.router)
    generate_router.init_service(service)
    return TestClient(app)


def _write_snapshot(service: GenerationService, task_id: str, step: int) -> Path:
    snap = service._task_dir(task_id) / f"step_{step}" / "preview.png"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(PNG_BYTES)
    return snap


# ─── 快照路由 ───


def test_snapshot_ok(client, service):
    _write_snapshot(service, "task_abc123", 3)
    resp = client.get("/api/generate/task_abc123/steps/3/snapshot")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG_BYTES


def test_snapshot_not_generated_returns_404_json(client):
    resp = client.get("/api/generate/task_abc123/steps/4/snapshot")
    assert resp.status_code == 404
    assert "暂无快照" in resp.json()["detail"]


def test_snapshot_task_not_found(client):
    resp = client.get("/api/generate/no_such_task/steps/3/snapshot")
    assert resp.status_code == 404
    assert "任务不存在" in resp.json()["detail"]


@pytest.mark.parametrize("step", [0, 9, -1])
def test_snapshot_step_out_of_range(client, step):
    resp = client.get(f"/api/generate/task_abc123/steps/{step}/snapshot")
    assert resp.status_code == 400


def test_snapshot_output_subdir_fallback(client, service):
    """快照落在 step_N/output/ 下也能命中（适配层候选路径）"""
    snap = (
        service._task_dir("task_abc123") / "step_3" / "output" / "preview.png"
    )
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(PNG_BYTES)
    resp = client.get("/api/generate/task_abc123/steps/3/snapshot")
    assert resp.status_code == 200


# ─── 任务详情 steps 快照标记（只加不改） ───


def test_task_detail_carries_snapshot_flags(client, service):
    _write_snapshot(service, "task_abc123", 3)
    resp = client.get("/api/generate/task_abc123")
    assert resp.status_code == 200
    body = resp.json()
    steps = {s["step"]: s for s in body["steps"]}

    # 新增字段
    assert steps[3]["snapshot_available"] is True
    assert steps[3]["snapshot_url"] == "/api/generate/task_abc123/steps/3/snapshot"
    assert steps[4]["snapshot_available"] is False
    assert steps[4]["snapshot_url"] is None

    # 既有字段未被改动
    assert steps[3]["name"] == "view_project"
    assert steps[3]["status"] == "completed"
    assert steps[3]["duration_ms"] == 1234
    assert steps[3]["output_data"]["drawing_path"] == "E:/out/demo.SLDDRW"
    assert body["task_id"] == "task_abc123"
    assert body["status"] == "running"

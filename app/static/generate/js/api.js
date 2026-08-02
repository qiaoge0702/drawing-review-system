/**
 * api.js — REST API 封装层
 * 唯一与后端 HTTP 交互的模块，视图层不直接 fetch。
 * M3 迁移 Vue3 时本文件原样保留。
 */

const BASE = '/api/generate';

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}

export const api = {
  /** 创建生成任务 @returns {{task_id: string, status: string}} */
  createTask(sourceFile, config = {}) {
    return request('', {
      method: 'POST',
      body: JSON.stringify({ source_file: sourceFile, config }),
    });
  },

  /** 任务详情 @returns {TaskResult} */
  getTask(taskId) {
    return request(`/${taskId}`);
  },

  /** 任务列表 @returns {{total: number, tasks: TaskResult[]}} */
  listTasks() {
    return request('');
  },

  /** 单步重跑 */
  rerun(taskId, fromStep, parameterOverrides = null) {
    return request(`/${taskId}/rerun`, {
      method: 'POST',
      body: JSON.stringify({ from_step: fromStep, parameter_overrides: parameterOverrides }),
    });
  },

  /** 步骤真图快照 URL（方案B，直接用于 <img src>） */
  snapshotUrl(taskId, step) {
    return `${BASE}/${taskId}/steps/${step}/snapshot`;
  },

  /** 产物下载 URL（直接用于 <a href> 或 <img src>） */
  artifactUrl(taskId, step, filename) {
    return `${BASE}/${taskId}/artifacts/${step}/${encodeURIComponent(filename)}`;
  },
};

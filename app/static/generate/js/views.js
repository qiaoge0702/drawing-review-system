/**
 * views.js — 视图渲染层
 * 每个渲染函数 = 未来 Vue 组件的雏形（M3 机械迁移）。
 * 只读取 store，不直接发请求（经 api.js / actions）。
 */

import { store } from './store.js';
import { api } from './api.js';

const STEP_META = [
  { num: 1, name: '3D模型加载' },
  { num: 2, name: '几何解析' },
  { num: 3, name: '视图投影' },
  { num: 4, name: '尺寸标注' },
  { num: 5, name: 'BOM生成' },
  { num: 6, name: '技术要求' },
  { num: 7, name: 'DXF构建' },
  { num: 8, name: '审查闭环' },
];

const STATUS_LABEL = {
  pending: '等待',
  running: '运行',
  completed: '成功',
  error: '失败',
  skipped: '跳过',
};

const STATUS_ICON = {
  pending: '○',
  running: '🔄',
  completed: '✅',
  error: '❌',
  skipped: '⏭️',
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** 步骤导航栏 */
export function renderStepNav(el) {
  const task = store.get('task');
  const activeStep = store.get('activeStep');
  const steps = task?.steps || [];
  const byNum = Object.fromEntries(steps.map((s) => [s.step, s]));

  el.innerHTML = STEP_META.map((m) => {
    const s = byNum[m.num];
    const status = s ? s.status : 'pending';
    const active = activeStep === m.num ? ' active' : '';
    return `
      <div class="step-item${active}" data-step="${m.num}">
        <span class="step-icon">${STATUS_ICON[status] || '○'}</span>
        <div class="step-text">
          <div class="step-title">Step ${m.num} ${esc(m.name)}</div>
          <div class="step-sub">${s ? esc(s.status) + (s.duration_ms ? ` · ${(s.duration_ms / 1000).toFixed(1)}s` : '') : '等待'}</div>
        </div>
      </div>`;
  }).join('');
}

/** 总进度条 */
export function renderProgress(el) {
  const task = store.get('task');
  const progress = task?.progress ?? 0;
  const status = task?.status ?? '-';
  el.innerHTML = `
    <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
    <div class="progress-text">总进度 ${progress}% · 状态 ${esc(status)}</div>`;
}

/** 主内容区（按选中步骤渲染） */
export function renderMain(el) {
  const task = store.get('task');
  const activeStep = store.get('activeStep');

  if (!task) {
    el.innerHTML = '<div class="empty">请创建或选择一个生成任务</div>';
    return;
  }

  if (activeStep === 0) {
    el.innerHTML = renderOverview(task) + renderTimeline(task) + renderFinalCard(task);
    return;
  }

  const step = task.steps.find((s) => s.step === activeStep);
  if (!step) {
    el.innerHTML = `<div class="empty">Step ${activeStep} 尚未执行</div>`;
    return;
  }
  el.innerHTML = renderStepDetail(task, step);
}

/** 步骤回放时间线：Step1-8 卡片，成功即显示真图快照（懒加载，点击看大图） */
function renderTimeline(task) {
  const steps = task.steps || [];
  const byNum = Object.fromEntries(steps.map((s) => [s.step, s]));

  const cards = STEP_META.map((m) => {
    const s = byNum[m.num];
    const status = s ? s.status : 'pending';
    const label = STATUS_LABEL[status] || status;
    const duration = s && s.duration_ms ? ` · ${(s.duration_ms / 1000).toFixed(1)}s` : '';
    // 快照 URL 以后端下发为准；兼容旧任务（无 snapshot_url 字段）按约定拼接
    const snapUrl = s && (s.snapshot_url || (s.snapshot_available
      ? api.snapshotUrl(task.task_id, m.num) : null));
    const media = snapUrl
      ? `<img class="snapshot-thumb" loading="lazy" src="${snapUrl}" data-full="${snapUrl}" alt="Step ${m.num} 快照">`
      : `<div class="snapshot-placeholder">${status === 'completed' ? '快照生成中…' : '暂无快照'}</div>`;
    return `
      <div class="timeline-card status-${esc(status)}">
        <div class="timeline-head">
          <span class="step-icon">${STATUS_ICON[status] || '○'}</span>
          <span class="timeline-title">Step ${m.num} ${esc(m.name)}</span>
        </div>
        <div class="timeline-sub">${esc(label)}${duration}</div>
        ${media}
        ${s && s.error ? `<div class="error-text">${esc(s.error)}</div>` : ''}
      </div>`;
  }).join('');

  return `<h3 class="timeline-section-title">步骤回放</h3><div class="timeline">${cards}</div>`;
}

/** 终图卡片：任务完成后展示 SLDDRW/DWG/PDF 下载链接 + 终图快照 */
function renderFinalCard(task) {
  if (task.status !== 'completed') return '';

  // 终图产物：扫描全部步骤产物，按扩展名匹配（以后端实际注册产物为准，不依赖字段名）
  const FINAL_EXTS = ['.slddrw', '.dwg', '.pdf'];
  const links = [];
  let finalSnap = null;
  for (const s of task.steps || []) {
    for (const a of s.artifacts || []) {
      const lower = (a.name || '').toLowerCase();
      if (FINAL_EXTS.some((ext) => lower.endsWith(ext))) {
        links.push(
          `<a class="btn btn-sm final-dl" href="${api.artifactUrl(task.task_id, s.step, a.name)}" target="_blank">下载 ${esc(a.name)}</a>`
        );
      }
    }
    if (s.snapshot_available || s.snapshot_url) finalSnap = s.snapshot_url || api.snapshotUrl(task.task_id, s.step);
  }

  return `
    <div class="final-card">
      <h3 class="timeline-section-title">终图验收</h3>
      ${finalSnap
        ? `<img class="snapshot-thumb final-snap" loading="lazy" src="${finalSnap}" data-full="${finalSnap}" alt="终图快照">`
        : '<div class="snapshot-placeholder">终图快照未生成</div>'}
      <div class="final-links">${links.join('') || '<span class="muted">无 SLDDRW/DWG/PDF 产物</span>'}</div>
    </div>`;
}

function renderOverview(task) {
  return `
    <h3>任务总览</h3>
    <table class="kv">
      <tr><td>任务ID</td><td>${esc(task.task_id)}</td></tr>
      <tr><td>源文件</td><td>${esc(task.source_file)}</td></tr>
      <tr><td>状态</td><td>${esc(task.status)}</td></tr>
      <tr><td>当前步骤</td><td>${task.current_step} / 8</td></tr>
      <tr><td>图纸类型</td><td>${esc(task.config?.drawing_type)}</td></tr>
      <tr><td>目标格式</td><td>${esc(task.config?.target_format)}</td></tr>
      ${task.error ? `<tr><td>错误</td><td class="error-text">${esc(task.error)}</td></tr>` : ''}
    </table>`;
}

function renderStepDetail(task, step) {
  const artifacts = (step.artifacts || []).map((a) => {
    const url = api.artifactUrl(task.task_id, step.step, a.name);
    const preview = a.type === 'png'
      ? `<img class="artifact-img" src="${url}" alt="${esc(a.name)}">`
      : '';
    return `<li>${preview}<a href="${url}" target="_blank">${esc(a.name)}</a> <span class="muted">(${(a.size / 1024).toFixed(1)} KB)</span></li>`;
  }).join('');

  const outputJson = step.output_data
    ? `<pre class="json-view">${esc(JSON.stringify(step.output_data, null, 2))}</pre>`
    : '<div class="muted">无输出数据</div>';

  return `
    <div class="step-header">
      <h3>Step ${step.step} · ${esc(step.name)}</h3>
      <button class="btn btn-sm" data-rerun="${step.step}" ${task.status === 'running' ? 'disabled' : ''}>从此步重跑</button>
    </div>
    <div class="step-status-line">状态: ${esc(step.status)} · 耗时 ${(step.duration_ms / 1000).toFixed(1)}s · 执行 ${step.execution_count} 次</div>
    ${(step.snapshot_available || step.snapshot_url)
      ? `<img class="snapshot-thumb" style="max-width:480px;height:auto;max-height:360px" loading="lazy" src="${step.snapshot_url || api.snapshotUrl(task.task_id, step.step)}" data-full="${step.snapshot_url || api.snapshotUrl(task.task_id, step.step)}" alt="Step ${step.step} 快照">`
      : ''}
    ${step.error ? `<div class="error-text">错误: ${esc(step.error)}</div>` : ''}
    <h4>产物</h4>
    <ul class="artifact-list">${artifacts || '<li class="muted">无产物</li>'}</ul>
    <h4>输出数据</h4>
    ${outputJson}`;
}

/** 历史任务列表 */
export function renderHistory(el, tasks) {
  if (!tasks.length) {
    el.innerHTML = '<div class="muted">暂无历史任务</div>';
    return;
  }
  el.innerHTML = tasks.map((t) => `
    <div class="history-item" data-task="${esc(t.task_id)}">
      <span class="history-id">${esc(t.task_id)}</span>
      <span class="history-status status-${esc(t.status)}">${esc(t.status)}</span>
      <span class="muted">${esc((t.source_file || '').split(/[\\/]/).pop())}</span>
    </div>`).join('');
}

/** WS 连接指示 */
export function renderWsStatus(el) {
  const connected = store.get('wsConnected');
  el.innerHTML = `<span class="ws-dot ${connected ? 'on' : 'off'}"></span>${connected ? '实时推送已连接' : '实时推送未连接'}`;
}

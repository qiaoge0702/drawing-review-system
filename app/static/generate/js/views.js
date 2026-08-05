/**
 * views.js — 视图渲染层
 * 阶段产物页为主视图，步骤明细仅作折叠展开；全程不展示 "Step N" 编号。
 */

import { store } from './store.js';
import { api } from './api.js';

// 步骤元数据（内部编号仅用于匹配后端数据，不渲染）
const STEP_META = [
  { num: 1, name: '模型加载' },
  { num: 2, name: '几何解析' },
  { num: 3, name: '视图投影' },
  { num: 4, name: '尺寸标注' },
  { num: 5, name: 'BOM生成' },
  { num: 6, name: '技术要求' },
  { num: 7, name: '图纸收尾' },
  { num: 8, name: '审查闭环' },
];

export const STEP_NAME_BY_NUM = Object.fromEntries(STEP_META.map((m) => [m.num, m.name]));

/** 阶段分组（显示顺序，非执行顺序） */
export const STAGE_GROUPS = [
  { key: 'import', name: '导入', steps: [1, 2] },
  { key: 'skeleton', name: '骨架', steps: [3, 7] },
  { key: 'table', name: '表格', steps: [5, 6] },
  { key: 'annotation', name: '标注', steps: [4] },
  { key: 'review', name: '审查', steps: [8] },
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

/** 聚合阶段状态：任一 running→运行；任一 error→失败；全部 completed→成功；否则等待 */
export function deriveStageStatus(stageSteps, expectedCount) {
  if (stageSteps.some((s) => s.status === 'running')) return 'running';
  if (stageSteps.some((s) => s.status === 'error')) return 'error';
  if (stageSteps.length === expectedCount && stageSteps.every((s) => s.status === 'completed')) return 'completed';
  if (stageSteps.some((s) => s.status === 'completed')) return 'running';
  return 'pending';
}

/** 阶段导航栏（无 Step 编号） */
export function renderStepNav(el) {
  const task = store.get('task');
  const activeStage = store.get('activeStage');
  const steps = task?.steps || [];
  const byNum = Object.fromEntries(steps.map((s) => [s.step, s]));

  el.innerHTML = STAGE_GROUPS.map((g) => {
    const stageSteps = g.steps.map((n) => byNum[n]).filter(Boolean);
    const status = deriveStageStatus(stageSteps, g.steps.length);
    const active = activeStage === g.key ? ' active' : '';
    return `
      <div class="step-item${active}" data-stage="${esc(g.key)}">
        <span class="step-icon">${STATUS_ICON[status] || '○'}</span>
        <div class="step-text">
          <div class="step-title">${esc(g.name)}</div>
          <div class="step-sub">${STATUS_LABEL[status] || status}</div>
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

/** 主内容区 */
export function renderMain(el) {
  const task = store.get('task');
  const activeStep = store.get('activeStep');

  if (!task) {
    el.innerHTML = '<div class="empty">请创建或选择一个生成任务</div>';
    return;
  }

  if (activeStep === 0) {
    el.innerHTML = renderOverview(task) + renderStage(task);
    return;
  }

  const step = task.steps.find((s) => s.step === activeStep);
  if (!step) {
    el.innerHTML = `<div class="empty">${esc(STEP_NAME_BY_NUM[activeStep] || '该步骤')} 尚未执行</div>`;
    return;
  }
  el.innerHTML = renderStepDetail(task, step);
}

/** 任务总览（无 Step 编号） */
function renderOverview(task) {
  return `
    <h3>任务总览</h3>
    <table class="kv">
      <tr><td>任务ID</td><td>${esc(task.task_id)}</td></tr>
      <tr><td>源文件</td><td>${esc(task.source_file)}</td></tr>
      <tr><td>状态</td><td>${esc(task.status)}</td></tr>
      <tr><td>当前步骤</td><td>${esc(STEP_NAME_BY_NUM[task.current_step] || '-')}</td></tr>
      ${task.error ? `<tr><td>错误</td><td class="error-text">${esc(task.error)}</td></tr>` : ''}
    </table>`;
}

/** 阶段产物页：唯一主视图 */
function renderStage(task) {
  const steps = task.steps || [];
  const byNum = Object.fromEntries(steps.map((s) => [s.step, s]));

  const cards = STAGE_GROUPS.map((g) => {
    const stageSteps = g.steps.map((n) => byNum[n]).filter(Boolean);
    const status = deriveStageStatus(stageSteps, g.steps.length);
    return `
      <div class="stage-card status-${esc(status)}" id="stage-card-${esc(g.key)}" data-stage="${esc(g.key)}">
        <div class="stage-head">
          <span class="stage-title">${esc(g.name)}</span>
          <span class="stage-sub">${g.steps.map((n) => esc(STEP_NAME_BY_NUM[n])).join(' / ')}</span>
        </div>
        <div class="stage-status">${STATUS_ICON[status] || '○'} ${STATUS_LABEL[status] || status}</div>
        <div class="stage-body">${renderStageBody(task, g, stageSteps, byNum)}</div>
        <div class="stage-details" id="details-${g.key}">
          <div class="details-toggle" data-toggle-details="${g.key}">▸ 步骤明细</div>
          <div class="details-content hidden" id="details-content-${g.key}">
            ${renderStageDetails(task, g, byNum)}
          </div>
        </div>
      </div>`;
  }).join('');

  return `<h3 class="timeline-section-title">阶段产物</h3><div class="stage-grid">${cards}</div>`;
}

function renderStageBody(task, group, stageSteps, byNum) {
  switch (group.key) {
    case 'import': return renderImportStage(task, stageSteps);
    case 'skeleton': return renderSkeletonStage(task, group.steps, byNum);
    case 'table': return renderTableStage(task, group.steps, byNum);
    case 'annotation': return renderAnnotationStage(task, group.steps, byNum);
    case 'review': return renderReviewStage(task, stageSteps);
    default: return '';
  }
}

function statusText(step) {
  if (!step) return '等待';
  return `${STATUS_ICON[step.status] || '○'} ${STATUS_LABEL[step.status] || step.status}`;
}

function renderImportStage(task, stageSteps) {
  const stepMap = Object.fromEntries(stageSteps.map((s) => [s.step, s]));
  const rows = [
    { k: '源文件', v: task.source_file || '-' },
    { k: '模型加载', v: statusText(stepMap[1]) },
    { k: '几何解析', v: statusText(stepMap[2]) },
  ];
  const summary = stepMap[2]?.output_data?.geometry_summary || stepMap[2]?.output_data?.bom_summary;
  if (summary) rows.push({ k: '几何解析摘要', v: JSON.stringify(summary) });
  return `<dl class="stage-dl">${rows.map((r) => `<dt>${esc(r.k)}</dt><dd>${esc(r.v)}</dd>`).join('')}</dl>`;
}

function _findVersionArtifact(task, stepNum, filename) {
  const step = (task.steps || []).find((s) => s.step === stepNum);
  if (!step) return null;
  const a = (step.artifacts || []).find((art) => art.name === filename);
  if (a) return api.artifactUrl(task.task_id, stepNum, a.name);
  return null;
}

function renderSkeletonStage(task, stepNums, byNum) {
  // 默认大图：优先 Step7 快照，回退 Step3
  const s7 = byNum[7];
  const s3 = byNum[3];
  const snapUrl = (s7?.snapshot_url || s3?.snapshot_url ||
    (s7?.snapshot_available ? api.snapshotUrl(task.task_id, 7) : null) ||
    (s3?.snapshot_available ? api.snapshotUrl(task.task_id, 3) : null));

  const media = snapUrl
    ? `<img class="snapshot-thumb stage-main-snap" loading="lazy" src="${snapUrl}" data-full="${snapUrl}" alt="骨架视图">`
    : '<div class="stage-empty">暂无骨架视图快照</div>';

  const dlUrl = _findVersionArtifact(task, 7, 'step7_skeleton.slddrw');
  const downloads = dlUrl
    ? `<div class="stage-downloads"><a class="btn btn-sm final-dl" href="${dlUrl}" target="_blank">下载骨架版 SLDDRW</a></div>`
    : '<div class="stage-empty">暂无骨架版产物</div>';

  return media + downloads;
}

function renderTableStage(task, stepNums, byNum) {
  const s5 = byNum[5];
  const s6 = byNum[6];

  let bomHtml = '<div class="stage-empty">BOM 数据待生成</div>';
  const bomTable = s5?.output_data?.bom_table;
  if (bomTable && bomTable.rows?.length) {
    const cols = bomTable.columns || [];
    const rows = bomTable.rows || [];
    bomHtml = `
      <table class="bom-table">
        <thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr class="${String(r[cols.length - 1] || '').includes('外购') ? 'external-part' : ''}">
              ${r.map((cell) => `<td>${esc(cell)}</td>`).join('')}
            </tr>`).join('')}
        </tbody>
      </table>`;
  }

  let techHtml = '<div class="stage-empty">技术要求待生成</div>';
  const tech = s6?.output_data?.tech_requirements;
  if (tech && tech.content?.length) {
    techHtml = `
      <div class="tech-block">
        <div class="tech-title">${esc(tech.template_name)}</div>
        <pre class="tech-content">${esc(tech.content.join('\n'))}</pre>
      </div>`;
  }

  const links = [];
  const s5url = _findVersionArtifact(task, 5, 'step5_table.slddrw');
  const s6url = _findVersionArtifact(task, 6, 'step6_table.slddrw');
  if (s5url) links.push(`<a class="btn btn-sm final-dl" href="${s5url}" target="_blank">下载 BOM 版 SLDDRW</a>`);
  if (s6url) links.push(`<a class="btn btn-sm final-dl" href="${s6url}" target="_blank">下载技术要求版 SLDDRW</a>`);
  const downloads = links.length ? `<div class="stage-downloads">${links.join('')}</div>` : '';

  return `<div class="stage-section-title">BOM 表</div>${bomHtml}` +
    `<div class="stage-section-title">技术要求</div>${techHtml}${downloads}`;
}

function renderAnnotationStage(task, stepNums, byNum) {
  const s4 = byNum[4];
  const snapUrl = s4?.snapshot_url || (s4?.snapshot_available ? api.snapshotUrl(task.task_id, 4) : null);
  const media = snapUrl
    ? `<img class="snapshot-thumb stage-main-snap" loading="lazy" src="${snapUrl}" data-full="${snapUrl}" alt="标注视图">`
    : '<div class="stage-empty">暂无标注视图快照</div>';

  const links = [];
  for (const filename of ['step4_final.slddrw', 'drawing.dwg', 'drawing.pdf']) {
    const url = _findVersionArtifact(task, 4, filename);
    if (url) {
      const label = filename.endsWith('.slddrw') ? '终版 SLDDRW' :
        filename.endsWith('.dwg') ? '终版 DWG' : '终版 PDF';
      links.push(`<a class="btn btn-sm final-dl" href="${url}" target="_blank">下载 ${esc(label)}</a>`);
    }
  }
  const pngUrl = _findVersionArtifact(task, 4, 'final_snapshot.png');
  if (pngUrl) {
    links.push(`<a class="btn btn-sm final-dl" href="${pngUrl}" target="_blank">下载终版 PNG</a>`);
  }
  const downloads = links.length
    ? `<div class="stage-downloads">${links.join('')}</div>`
    : '<div class="stage-empty">暂无终版图产物</div>';

  return media + downloads;
}

function renderReviewStage(task, stageSteps) {
  const step = stageSteps[0];
  if (!step) return '<div class="stage-empty">审查闭环尚未执行</div>';
  const output = step.output_data
    ? `<pre class="json-view stage-report">${esc(JSON.stringify(step.output_data, null, 2))}</pre>`
    : '<div class="muted">暂无审查报告数据</div>';
  return `
    <div class="stage-status-line">状态: ${statusText(step)}</div>
    ${step.error ? `<div class="error-text">${esc(step.error)}</div>` : ''}
    ${output}`;
}

/** 折叠区：阶段内步骤明细（无 Step 编号） */
function renderStageDetails(task, group, byNum) {
  const items = group.steps.map((n) => {
    const s = byNum[n];
    if (!s) return `<div class="detail-item"><span class="detail-name">${esc(STEP_NAME_BY_NUM[n])}</span><span class="muted">等待</span></div>`;
    const duration = s.duration_ms ? `${(s.duration_ms / 1000).toFixed(1)}s` : '-';
    const warnings = (s.output_data?.warnings || []).length;
    const warnText = warnings ? ` · ${warnings} 条警告` : '';
    const snapUrl = s.snapshot_url || (s.snapshot_available ? api.snapshotUrl(task.task_id, n) : null);
    const snap = snapUrl
      ? `<img class="detail-snap" loading="lazy" src="${snapUrl}" data-full="${snapUrl}" alt="${esc(STEP_NAME_BY_NUM[n])} 快照">`
      : '';
    const versionLinks = [];
    const versionFiles = {
      7: 'step7_skeleton.slddrw',
      5: 'step5_table.slddrw',
      6: 'step6_table.slddrw',
      4: 'step4_final.slddrw',
    };
    const vf = versionFiles[n];
    if (vf) {
      const url = _findVersionArtifact(task, n, vf);
      if (url) versionLinks.push(`<a href="${url}" target="_blank">${esc(vf)}</a>`);
    }
    const versionHtml = versionLinks.length ? `<div class="detail-version">${versionLinks.join('')}</div>` : '';
    return `
      <div class="detail-item status-${esc(s.status)}">
        <div class="detail-head">
          <span class="detail-status">${STATUS_ICON[s.status] || '○'}</span>
          <span class="detail-name">${esc(STEP_NAME_BY_NUM[n])}</span>
          <span class="detail-meta">${esc(STATUS_LABEL[s.status] || s.status)} · ${esc(duration)}${warnText}</span>
        </div>
        ${snap}
        ${versionHtml}
        ${s.error ? `<div class="error-text">${esc(s.error)}</div>` : ''}
      </div>`;
  }).join('');
  return `<div class="details-list">${items}</div>`;
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
      <h3>${esc(STEP_NAME_BY_NUM[step.step] || step.name)}</h3>
      <button class="btn btn-sm" data-rerun="${step.step}" ${task.status === 'running' ? 'disabled' : ''}>从此步重跑</button>
    </div>
    <div class="step-status-line">状态: ${STATUS_ICON[step.status] || '○'} ${STATUS_LABEL[step.status] || esc(step.status)} · 耗时 ${(step.duration_ms / 1000).toFixed(1)}s · 执行 ${step.execution_count} 次</div>
    ${(step.snapshot_available || step.snapshot_url)
      ? `<img class="snapshot-thumb" style="max-width:480px;height:auto;max-height:360px" loading="lazy" src="${step.snapshot_url || api.snapshotUrl(task.task_id, step.step)}" data-full="${step.snapshot_url || api.snapshotUrl(task.task_id, step.step)}" alt="${esc(STEP_NAME_BY_NUM[step.step] || step.name)} 快照">`
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

/**
 * main.js — 入口/动作层
 * 事件绑定 + 协调 api / ws / store / views。
 */

import { api } from './api.js';
import { store } from './store.js';
import * as ws from './ws.js';
import { renderStepNav, renderProgress, renderMain, renderHistory, renderWsStatus, STEP_NAME_BY_NUM } from './views.js';

const $ = (sel) => document.querySelector(sel);

// ─── 渲染订阅 ───

function renderAll() {
  renderStepNav($('#step-nav'));
  renderProgress($('#progress-area'));
  renderMain($('#main-content'));
  renderWsStatus($('#ws-status'));
}

store.subscribe(renderAll);

// ─── 动作 ───

async function createTask() {
  const sourceFile = $('#source-file').value.trim();
  if (!sourceFile) {
    alert('请输入 SW 文件路径');
    return;
  }
  try {
    const { task_id } = await api.createTask(sourceFile, {});
    await openTask(task_id);
  } catch (e) {
    alert(`创建任务失败: ${e.message}`);
  }
}

async function openTask(taskId) {
  store.set({ taskId, activeStep: 0, activeStage: null, task: null });
  try {
    const task = await api.getTask(taskId);
    store.set({ task });
    if (!['completed', 'error'].includes(task.status)) {
      ws.connect(taskId);
    }
  } catch (e) {
    alert(`加载任务失败: ${e.message}`);
  }
}

async function rerunFrom(step) {
  const taskId = store.get('taskId');
  if (!taskId) return;
  try {
    await api.rerun(taskId, step);
    ws.connect(taskId);
  } catch (e) {
    alert(`重跑失败: ${e.message}`);
  }
}

async function loadHistory() {
  try {
    const { tasks } = await api.listTasks();
    store.set({ tasks });
    renderHistory($('#history-list'), tasks);
  } catch (e) {
    console.error('加载历史失败', e);
  }
}

// ─── 事件绑定 ───

function bindEvents() {
  $('#btn-create').addEventListener('click', createTask);
  $('#btn-refresh-history').addEventListener('click', loadHistory);

  // 阶段导航（事件委托）
  $('#step-nav').addEventListener('click', (e) => {
    const item = e.target.closest('.step-item');
    if (!item) return;
    const stage = item.dataset.stage;
    store.set({ activeStep: 0, activeStage: stage });
    const card = $(`#stage-card-${stage}`);
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  // 总览按钮
  $('#btn-overview').addEventListener('click', () => store.set({ activeStep: 0 }));

  // 步骤明细折叠展开
  $('#main-content').addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-toggle-details]');
    if (!toggle) return;
    const key = toggle.dataset.toggleDetails;
    const content = $(`#details-content-${key}`);
    const isHidden = content.classList.toggle('hidden');
    toggle.textContent = isHidden ? '▸ 步骤明细' : '▾ 步骤明细';
  });

  // 重跑按钮（事件委托）
  $('#main-content').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-rerun]');
    if (!btn) return;
    const stepName = STEP_NAME_BY_NUM[Number(btn.dataset.rerun)] || `步骤 ${btn.dataset.rerun}`;
    if (confirm(`确定从「${stepName}」重跑吗？后续步骤的产物将被清除。`)) {
      rerunFrom(Number(btn.dataset.rerun));
    }
  });

  // 快照缩略图 → 大图灯箱（事件委托）
  $('#main-content').addEventListener('click', (e) => {
    const img = e.target.closest('.snapshot-thumb');
    if (!img) return;
    const box = $('#lightbox');
    box.querySelector('img').src = img.dataset.full || img.src;
    box.classList.remove('hidden');
  });
  $('#lightbox').addEventListener('click', () => {
    $('#lightbox').classList.add('hidden');
  });

  // 历史任务点击
  $('#history-list').addEventListener('click', (e) => {
    const item = e.target.closest('.history-item');
    if (item) openTask(item.dataset.task);
  });
}

// ─── 启动 ───

bindEvents();
renderAll();
loadHistory();

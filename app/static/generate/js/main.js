/**
 * main.js — 入口/动作层
 * 事件绑定 + 协调 api / ws / store / views。
 */

import { api } from './api.js';
import { store } from './store.js';
import * as ws from './ws.js';
import { renderStepNav, renderProgress, renderMain, renderHistory, renderWsStatus } from './views.js';

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
  store.set({ taskId, activeStep: 0, mainView: 'stage', task: null });
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

  // 步骤导航（事件委托）
  $('#step-nav').addEventListener('click', (e) => {
    const item = e.target.closest('.step-item');
    if (!item) return;
    store.set({ activeStep: Number(item.dataset.step) });
  });

  // 总览按钮
  $('#btn-overview').addEventListener('click', () => store.set({ activeStep: 0 }));

  // 主视图切换（阶段产物 / 步骤回放）
  $('#main-content').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-view]');
    if (!btn) return;
    store.set({ mainView: btn.dataset.view });
  });

  // 重跑按钮（事件委托）
  $('#main-content').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-rerun]');
    if (!btn) return;
    if (confirm(`确定从 Step ${btn.dataset.rerun} 重跑吗？后续步骤检查点将被清除。`)) {
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

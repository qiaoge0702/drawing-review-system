/**
 * ws.js — WebSocket 通道层
 * 负责连接管理、自动重连、消息分发到 store。
 */

import { store } from './store.js';
import { api } from './api.js';

let ws = null;
let reconnectTimer = null;
let currentTaskId = null;

const RECONNECT_DELAY = 3000;

export function connect(taskId) {
  disconnect();
  currentTaskId = taskId;
  _open();
}

export function disconnect() {
  currentTaskId = null;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  store.set({ wsConnected: false });
}

function _open() {
  if (!currentTaskId) return;

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/${currentTaskId}`);

  ws.onopen = () => {
    store.set({ wsConnected: true });
    // 保持连接（服务端等待接收消息）
    ws.send('ping');
  };

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    _handleMessage(msg);
  };

  ws.onclose = () => {
    store.set({ wsConnected: false });
    // 任务未结束时自动重连
    const task = store.get('task');
    if (currentTaskId && task && !['completed', 'error'].includes(task.status)) {
      reconnectTimer = setTimeout(_open, RECONNECT_DELAY);
    }
  };

  ws.onerror = () => {
    ws && ws.close();
  };
}

async function _handleMessage(msg) {
  const taskId = store.get('taskId');
  if (!taskId || msg.task_id !== taskId) return;

  switch (msg.type) {
    case 'step_start':
    case 'step':
    case 'finished':
      // 以服务端全量任务状态为准，拉取刷新（避免增量合并复杂度）
      try {
        const task = await api.getTask(taskId);
        store.set({ task });
      } catch (e) {
        console.error('刷新任务状态失败', e);
      }
      break;
    default:
      break;
  }
}

/**
 * store.js — 状态层
 * 单一数据源 + 订阅通知。M3 迁移时字段 1:1 平移到 Pinia。
 */

const state = {
  taskId: null,        // 当前任务 ID
  task: null,          // 当前 TaskResult
  activeStep: 0,       // 当前查看的步骤（0 = 总览）
  activeStage: null,   // 当前高亮/定位的阶段 key
  tasks: [],           // 历史任务列表
  wsConnected: false,
};

const listeners = new Set();

export const store = {
  get(key) {
    return state[key];
  },

  getAll() {
    return { ...state };
  },

  /** 更新状态并通知订阅者 @param {Object} patch */
  set(patch) {
    Object.assign(state, patch);
    listeners.forEach((fn) => fn(state));
  },

  /** 订阅状态变化 @returns {Function} 取消订阅函数 */
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

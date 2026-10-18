// 全局 Store（文档 3 第 9.1 节，v1.1：补 applyCharacterTheme import）。

import {applyCharacterTheme} from './theme.js';
import {api} from './api.js';

export function initStore() {
  Alpine.store('app', {
    // 用户
    user: null,
    token: localStorage.getItem('dudulu_token') || null,

    // 当前角色
    currentCharacter: null,
    // 角色列表（单一数据源：侧边栏、选择页、创建/归档后都读它，
    // 刷新统一走 loadCharacters()——2026-10-18 修"创建完侧边栏不更新"）
    characters: [],
    charactersLoading: false,

    // 当前对话
    messages: [],

    // 当前状态
    state: {
      expression: {},
      internal: {},
      affective: {},
    },

    // 最近一次状态增量，来自 state_update 的 delta（呼吸动画判据）
    lastDelta: null,

    // 主动消息未读：character_id -> 未读条数（红点数据源）
    unreadProactive: {},

    // UI
    ui: {
      sidebarOpen: false,
      statePanelOpen: false,
      loading: false,
    },

    // 方法
    setToken(token) {
      this.token = token;
      localStorage.setItem('dudulu_token', token);
    },

    clearToken() {
      this.token = null;
      localStorage.removeItem('dudulu_token');
    },

    markProactiveUnread(characterId) {
      this.unreadProactive[characterId] =
        (this.unreadProactive[characterId] || 0) + 1;
    },

    clearProactiveUnread(characterId) {
      delete this.unreadProactive[characterId];
    },

    setCurrentCharacter(character) {
      this.currentCharacter = character;
      applyCharacterTheme(character.theme);
    },

    /** 刷新角色列表（创建/归档/恢复后调用，侧边栏与选择页同步更新）。 */
    async loadCharacters() {
      this.charactersLoading = true;
      try {
        const result = await api.listCharacters();
        this.characters = result.characters || [];
      } catch (error) {
        console.warn('角色列表加载失败:', error.message);
      } finally {
        this.charactersLoading = false;
      }
    },
  });
}

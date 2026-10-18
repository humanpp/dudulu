// 角色选择页组件（文档 3 第 6.3 节；已归档视图为 2026-10-18 补充）。

import {api} from '../api.js';
import {registerPage, navigate, routeMatches} from '../router.js';
import {characterAvatar} from '../theme.js';
import {showError, showSuccess} from '../utils/toast.js';

export function characterSelectPage() {
  return {
    characters: [],
    archivedCharacters: [],   // 已归档（恢复入口）
    showArchived: false,
    loading: true,
    loadError: '',
    restoring: false,

    init() {
      registerPage('/characters', () => this.load());
      // 未登录（登录页）不预拉角色：等路由切换或登录后进入时再加载
      if (routeMatches('/characters') && Alpine.store('app').token) {
        this.load();
      }
    },

    async load() {
      this.loading = true;
      this.loadError = '';
      try {
        const store = Alpine.store('app');
        await store.loadCharacters();
        // 本页列表直接引用 store（单一数据源），避免两侧漂移
        this.characters = store.characters;
        // 顺带拉一次归档列表，用于底部「已归档 (N)」入口的计数
        await this.loadArchived();
      } catch (error) {
        this.loadError = error.message || '角色加载失败';
      } finally {
        this.loading = false;
      }
    },

    async loadArchived() {
      try {
        const result = await api.listCharacters(true);
        this.archivedCharacters = result.characters || [];
      } catch (error) {
        // 归档列表拉不到不影响主列表
        this.archivedCharacters = [];
      }
    },

    toggleArchived() {
      this.showArchived = !this.showArchived;
      if (this.showArchived) this.loadArchived();
    },

    async restore(character) {
      if (this.restoring) return;
      this.restoring = true;
      try {
        await api.restoreCharacter(character.id);
        showSuccess(`已恢复「${character.name}」`);
        await this.load();   // 内部会刷新 store → 侧边栏同步
        // 恢复后如果归档列表空了，自动回到正常列表
        if (!this.archivedCharacters.length) this.showArchived = false;
      } catch (error) {
        showError(error.message || '恢复失败');
      } finally {
        this.restoring = false;
      }
    },

    avatarOf(character) {
      return characterAvatar(character);
    },

    stageLabel(character) {
      return character.relationship_stage_label
        || character.stage_label || '陌生';
    },

    stageCode(character) {
      return character.relationship_stage_code
        || character.stage_code || 'S0';
    },

    stageProgress(character) {
      const affection = Number(character.affection ?? 0);
      return Math.max(0, Math.min(100, affection));
    },

    openChat(character) {
      navigate(`#/chat/${character.id}`);
    },

    openCreate() {
      navigate('#/character/create');
    },

    openSettings() {
      navigate('#/settings');
    },
  };
}

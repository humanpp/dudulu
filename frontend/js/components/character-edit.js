// 角色编辑页组件（文档 3 第 6.5 节）。

import {api} from '../api.js';
import {getCurrentCharacterId, registerPage, navigate,
        routeMatches} from '../router.js';
import {applyCharacterTheme} from '../theme.js';
import {showError, showSuccess} from '../utils/toast.js';
import {formatDateTime} from '../utils/format.js';

export function characterEditPage() {
  return {
    characterId: null,
    form: {
      name: '',
      nickname: '',
      gender: 'female',
      age: '',
      occupation: '',
      visibility: 'private',
      proactiveEnabled: true,
      intervalHours: '',
      eventSensitivity: 'standard',
      theme: {primary: '#C4A882', soft: '#E8DDD0', deep: '#8B7355'},
    },
    versions: [],
    loading: true,
    saving: false,

    // 情感参数预设（v1.1：角色级 event_sensitivity）
    sensitivityOptions: [
      {value: 'standard', label: '标准', scale: 1.0},
      {value: 'sensitive', label: '敏感', scale: 1.3},
      {value: 'restrained', label: '克制', scale: 0.8},
    ],

    init() {
      registerPage(/^\/character\/([^/]+)\/edit/, () => this.load());
      if (routeMatches(/^\/character\/[^/]+\/edit/)) this.load();
    },

    async load() {
      this.characterId = getCurrentCharacterId();
      if (!this.characterId) {
        navigate('#/characters');
        return;
      }
      this.loading = true;
      try {
        const character = await api.getCharacter(this.characterId);
        const profile = character.profile || character;
        this.form.name = profile.name || '';
        this.form.nickname = profile.nickname || '';
        this.form.gender = profile.gender || 'female';
        this.form.age = profile.age ?? '';
        this.form.occupation = profile.background?.occupation || '';
        this.form.visibility = profile.visibility?.is_public
          ? 'public' : 'private';
        this.form.proactiveEnabled = profile.proactive_enabled !== false;
        this.form.intervalHours =
          profile.emotion_overrides?.proactive?.interval_hours ?? '';
        this.form.theme = {
          primary: profile.theme?.primary || '#C4A882',
          soft: profile.theme?.soft || '#E8DDD0',
          deep: profile.theme?.deep || '#8B7355',
        };
        this.form.eventSensitivity = this.sensitivityFromProfile(profile);
        await this.loadVersions();
      } catch (error) {
        showError(error.message || '角色加载失败');
      } finally {
        this.loading = false;
      }
    },

    sensitivityFromProfile(profile) {
      // 情感参数预设：反查 emotion_overrides.event_sensitivity 的标量形态
      const overrides = profile.emotion_overrides?.event_sensitivity;
      if (!overrides || typeof overrides !== 'object') return 'standard';
      const first = Object.values(overrides)[0];
      if (typeof first !== 'number') return 'standard';
      if (first >= 1.3) return 'sensitive';
      if (first <= 0.8) return 'restrained';
      return 'standard';
    },

    async loadVersions() {
      try {
        const result = await api.getCharacterVersions(this.characterId);
        this.versions = (result.versions || []).slice(0, 20);
      } catch (error) {
        console.warn('版本历史加载失败:', error.message);
      }
    },

    sensitivityScale() {
      const option = this.sensitivityOptions.find(
        (item) => item.value === this.form.eventSensitivity);
      return option ? option.scale : 1.0;
    },

    async save() {
      if (this.saving || !this.form.name.trim()) return;
      this.saving = true;
      try {
        const updates = {
          name: this.form.name.trim(),
          nickname: this.form.nickname.trim() || null,
          gender: this.form.gender,
          age: this.form.age === '' ? null : Number(this.form.age),
          background: {occupation: this.form.occupation.trim()},
          proactive_enabled: this.form.proactiveEnabled,
          visibility: {is_public: this.form.visibility === 'public'},
          theme: {...this.form.theme},
          emotion_overrides: {
            // 频率：留空跟随全局（v1.1：写 interval_hours）
            proactive: {
              interval_hours: this.form.intervalHours === ''
                ? null : Number(this.form.intervalHours),
            },
            // 情感参数：作用于所有事件标签的冲击系数（文档 1 第 10.2 节）
            event_sensitivity: {all: this.sensitivityScale()},
          },
        };
        await api.updateCharacter(this.characterId, updates);
        applyCharacterTheme(this.form.theme);
        showSuccess('已保存');
        // 名字/职业等列表可见字段改了：刷新 store 让侧边栏与选择页同步
        await Alpine.store('app').loadCharacters();
        await this.loadVersions();
      } catch (error) {
        showError(error.message || '保存失败');
      } finally {
        this.saving = false;
      }
    },

    async resetThemeFromTemplate() {
      try {
        const character = await api.getCharacter(this.characterId);
        const templateId = character.generation_meta?.template_id;
        if (!templateId) {
          showError('该角色不是模板创建，无模板主题可重置');
          return;
        }
        const result = await api.listTemplates();
        const template = (result.templates || [])
          .find((item) => item.id === templateId);
        if (template?.theme) {
          this.form.theme = {...template.theme};
          applyCharacterTheme(this.form.theme);
        }
      } catch (error) {
        showError(error.message || '重置失败');
      }
    },

    async rollback(versionId) {
      try {
        await api.rollbackCharacter(this.characterId, versionId);
        showSuccess('已回滚');
        await this.load();
      } catch (error) {
        showError(error.message || '回滚失败');
      }
    },

    async exportCharacter() {
      try {
        const card = await api.exportCharacter(this.characterId);
        const blob = new Blob([JSON.stringify(card, null, 2)],
          {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `${this.form.name || 'character'}.json`;
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        showError(error.message || '导出失败');
      }
    },

    confirmArchive() {
      if (!window.confirm(`确定归档「${this.form.name}」？归档后可在列表中恢复。`)) {
        return;
      }
      this.archive();
    },

    async archive() {
      try {
        await api.archiveCharacter(this.characterId);
        showSuccess('已归档');
        // 侧边栏立刻移除该角色
        await Alpine.store('app').loadCharacters();
        navigate('#/characters');
      } catch (error) {
        showError(error.message || '归档失败');
      }
    },

    cancel() {
      window.history.back();
    },

    formatDateTime,
  };
}

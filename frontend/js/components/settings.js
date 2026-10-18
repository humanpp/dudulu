// 设置页组件（文档 3 第 6.9 节）。

import {api} from '../api.js';
import {wsManager} from '../ws.js';
import {registerPage, navigate, routeMatches} from '../router.js';
import {showError, showSuccess} from '../utils/toast.js';

export function settingsPage() {
  return {
    // 账号
    user: null,

    // 模型设置（v1.1：api_key 占位符态 + 保存语义；v1.4：base_url 可自行修改）
    llm: {
      mode: 'api',
      provider: 'openai',
      model: '',
      baseUrl: '',
      apiKeyInput: '',
      hasApiKey: false,
      apiKeyMasked: null,
      showApiKey: false,
    },
    llmDirty: false,

    // 主动行为（全局）
    proactive: {
      enabled: true,
      intervalHours: 4.5,
      minGapHours: 3.0,
      maxDaily: 3,
      silenceThreshold: 3,
      silenceRecoveryHours: 12,
      quietHours: [23, 8],
    },

    savingLlm: false,
    savingProactive: false,
    importing: false,

    // 修改密码模态框（Phase 10：替掉 window.prompt——明文可见）
    passwordModalOpen: false,
    passwordForm: {old: '', new: '', confirm: ''},
    passwordError: '',
    passwordSaving: false,

    init() {
      registerPage('/settings', () => this.load());
      if (routeMatches('/settings')) this.load();
    },

    async load() {
      const store = Alpine.store('app');
      try {
        const [llmConfig, proactiveConfig] = await Promise.all([
          api.getLLMConfig(),
          api.getProactiveConfig(),
        ]);
        this.llm.mode = llmConfig.mode || 'api';
        this.llm.provider = llmConfig.api?.provider || 'openai';
        this.llm.model = llmConfig.api?.model || '';
        this.llm.baseUrl = llmConfig.api?.base_url || '';
        this.llm.hasApiKey = Boolean(llmConfig.api?.has_api_key);
        this.llm.apiKeyMasked = llmConfig.api?.api_key_masked || null;
        this.llm.apiKeyInput = '';
        this.llmDirty = false;
        this.proactive = {...this.proactive, ...proactiveConfig};
      } catch (error) {
        showError(error.message || '配置加载失败');
      }
    },

    onApiKeyInput() {
      this.llmDirty = true;
    },

    toggleApiKeyVisibility() {
      this.llm.showApiKey = !this.llm.showApiKey;
    },

    async saveLLM() {
      if (this.savingLlm) return;
      this.savingLlm = true;
      try {
        const data = {mode: this.llm.mode};
        if (this.llm.mode === 'api') {
          const apiSection = {
            provider: this.llm.provider,
            model: this.llm.model,
            // base_url 也走用户覆盖：换网关/换服务商不用改 .env
            base_url: this.llm.baseUrl.trim(),
          };
          // 保存语义（v1.1）：没动 Key 输入框 → 不带 api_key 字段（保持原值）；
          // 清空后保存 → api_key: ''（清除）；输入新值 → 替换。
          if (this.llmDirty) apiSection.api_key = this.llm.apiKeyInput;
          data.api = apiSection;
        } else {
          data.local = {
            base_url: this.llm.baseUrl,
            model: this.llm.model,
          };
        }
        await api.updateLLMConfig(data);
        this.llmDirty = false;
        this.llm.apiKeyInput = '';
        await this.load();
        showSuccess('模型设置已保存');
      } catch (error) {
        showError(error.message || '保存失败');
      } finally {
        this.savingLlm = false;
      }
    },

    async saveProactive() {
      if (this.savingProactive) return;
      this.savingProactive = true;
      try {
        await api.updateProactiveConfig({
          enabled: this.proactive.enabled,
          interval_hours: Number(this.proactive.intervalHours),
          min_gap_hours: Number(this.proactive.minGapHours),
          max_daily: Number(this.proactive.maxDaily),
          silence_threshold: Number(this.proactive.silenceThreshold),
          silence_recovery_hours: Number(this.proactive.silenceRecoveryHours),
          quiet_hours: [
            Number(this.proactive.quietHours[0]),
            Number(this.proactive.quietHours[1]),
          ],
        });
        showSuccess('主动行为设置已保存');
      } catch (error) {
        showError(error.message || '保存失败');
      } finally {
        this.savingProactive = false;
      }
    },

    openPasswordModal() {
      this.passwordForm = {old: '', new: '', confirm: ''};
      this.passwordError = '';
      this.passwordModalOpen = true;
    },

    closePasswordModal() {
      this.passwordModalOpen = false;
    },

    async submitPassword() {
      this.passwordError = '';
      if (!this.passwordForm.old || !this.passwordForm.new) {
        this.passwordError = '请填写当前密码与新密码';
        return;
      }
      if (this.passwordForm.new !== this.passwordForm.confirm) {
        this.passwordError = '两次输入的新密码不一致';
        return;
      }
      if (this.passwordForm.new.length < 8) {
        this.passwordError = '新密码至少 8 位';
        return;
      }
      this.passwordSaving = true;
      try {
        await api.updatePassword({
          old_password: this.passwordForm.old,
          new_password: this.passwordForm.new,
        });
        this.passwordModalOpen = false;
        showSuccess('密码已修改');
      } catch (error) {
        this.passwordError = error.message || '修改失败';
      } finally {
        this.passwordSaving = false;
      }
    },

    // 退出登录不需要后端端点（v1.1 明确）
    logout() {
      const store = Alpine.store('app');
      store.clearToken();
      // 清掉当前用户的残留数据：否则换账号登录后侧边栏会闪一下别人的角色
      store.characters = [];
      store.currentCharacter = null;
      store.unreadProactive = {};
      wsManager.disconnect();
      navigate('#/login');
    },

    async importCharacter() {
      const input = document.createElement('input');
      input.type = 'file';
      // 只接受 JSON：下面只用 JSON.parse（YAML 会解析失败报错）
      input.accept = '.json';
      input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) return;
        this.importing = true;
        try {
          const text = await file.text();
          const card = JSON.parse(text);
          await api.importCharacter(card.profile || card);
          showSuccess('角色卡已导入');
          // 导入后刷新列表，侧边栏立刻可见
          await Alpine.store('app').loadCharacters();
        } catch (error) {
          showError(error.message || '导入失败：需要 JSON 角色卡');
        } finally {
          this.importing = false;
        }
      };
      input.click();
    },

    async exportAllCharacters() {
      try {
        const result = await api.listCharacters();
        const cards = [];
        for (const item of result.characters || []) {
          cards.push(await api.exportCharacter(item.id));
        }
        const blob = new Blob([JSON.stringify(cards, null, 2)],
          {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'dudulu-characters.json';
        anchor.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        showError(error.message || '导出失败');
      }
    },

    back() {
      window.history.back();
    },
  };
}

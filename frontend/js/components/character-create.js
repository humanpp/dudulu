// 角色创建向导组件（文档 3 第 6.4 / 9.7 节；三条创建路径见文档 1 第 13.3 节）。

import {api} from '../api.js';
import {setNestedValue, deepClone} from '../utils/object.js';
import {registerPage, navigate, routeMatches} from '../router.js';
import {showError} from '../utils/toast.js';

/** 空白档案：结构齐全、值安全（供向导 Step 2/3 的绑定初始化）。 */
function blankProfile() {
  return {
    name: '',
    nickname: '',
    gender: 'female',
    age: 22,
    // 体型用后端枚举 slim/athletic/curvy/plump，中文只在 <option> 里做显示
    appearance: {body_type: 'slim'},
    personality: {
      // 五维中性起点：空对象会让 x-for 一行都不渲染，
      // 「性格」组看着像消失了（2026-10-18 实测）；每维都带 🎲 可单独随机
      big_five: {openness: 0.5, conscientiousness: 0.5, extraversion: 0.5,
                 agreeableness: 0.5, neuroticism: 0.5},
      traits: [],
    },
    background: {occupation: '', current_state: ''},
    speech_style: {catchphrases: []},
    visibility: {is_public: false},
  };
}

// 体型的取值是后端枚举（slim/athletic/curvy/plump），中文只做显示。

/** 随机化用的回退模板（空白创建没有模板时，借用池子生成随机值）。 */
const FALLBACK_TEMPLATE = 'gentle_artist';

/**
 * 随机接口的字段名 → 档案里的实际路径。
 * 不加这层映射，body_type/occupation 等会被写到档案顶层，
 * 而界面绑定的是 appearance.body_type / background.occupation，
 * 于是「点了没反应」（2026-10-18 实测）。
 */
const FIELD_PATHS = {
  name: 'name',
  gender: 'gender',
  age: 'age',
  body_type: 'appearance.body_type',
  height: 'appearance.height_cm',
  hair: 'appearance.hair',
  occupation: 'background.occupation',
  living_situation: 'background.living_situation',
  key_experiences: 'background.key_experiences',
  big_five: 'personality.big_five',
  tone: 'speech_style.tone',
  pace: 'speech_style.pace',
  catchphrases: 'speech_style.catchphrases',
  initial_affection: 'relationship_defaults.initial_affection',
};

export function characterCreatePage() {
  return {
    currentStep: 0,
    steps: ['模板', '配置', '预览'],
    templates: [],
    selectedTemplate: null,
    // 创建模式：template（从模板）/ manual（空白手填 / 随机后微调）
    mode: 'template',
    // 空白档案作初值：不能是 null——index.html 里 Step 2/3 的绑定
    // 在 Alpine 初始化时就会被求值（隐藏元素也会），null 会抛 TypeError
    // 导致整个组件树初始化中断、页面全白。
    profile: blankProfile(),
    traitsText: '',            // 性格特质的文本形态（顿号分隔）
    lockedFields: new Set(),
    validationIssues: [],
    profileValid: true,
    submitting: false,
    creatingRandom: false,

    init() {
      // 每次进入创建页都重置：否则第二次点「新建」还停在刚才的预览步、
      // 带着上一个角色的档案（2026-10-18 实测）
      registerPage('/character/create', () => {
        this.reset();
        this.loadTemplates();
      });
      if (routeMatches('/character/create')) {
        this.reset();
        this.loadTemplates();
      }
    },

    /** 重置向导到初始状态（换入口/再次进入时调用）。 */
    reset() {
      this.currentStep = 0;
      this.selectedTemplate = null;
      this.mode = 'template';
      this.profile = blankProfile();
      this.traitsText = '';
      this.lockedFields = new Set();
      this.validationIssues = [];
      this.profileValid = true;
      this.submitting = false;
      this.creatingRandom = false;
    },

    async loadTemplates() {
      try {
        const result = await api.listTemplates();
        this.templates = result.templates || [];
      } catch (error) {
        showError(error.message || '模板加载失败');
      }
    },

    // ── 入口一：从模板创建 ──────────────────────────────────
    selectTemplate(id) {
      this.selectedTemplate = id;
      this.mode = 'template';
      const template = this.templates.find((item) => item.id === id);
      if (!template) return;
      // 在空白档案上浅合并：模板缺节时绑定仍有安全结构可读
      const merged = {...blankProfile(),
                      ...deepClone(template.defaults || {})};
      // 模板给的是 age_range（区间），向导要一个具体年龄：取中点
      if (!merged.age && Array.isArray(merged.age_range)) {
        merged.age = Math.round(
          (merged.age_range[0] + merged.age_range[1]) / 2);
      }
      this.profile = merged;
      this.profile.theme = template.theme;
      this.profile.visibility = {is_public: false};
      this.traitsText = (this.profile.personality?.traits || []).join('、');
      this.validate();
      this.nextStep();
    },

    // ── 入口二：随机创建（一键：随机模板 + 随机种子 → 直接落库）──
    async createRandomNow() {
      if (this.creatingRandom || !this.templates.length) return;
      this.creatingRandom = true;
      try {
        const template = this.templates[
          Math.floor(Math.random() * this.templates.length)];
        const seed = Math.floor(Math.random() * 1000000);
        const result = await api.createRandom({
          template_id: template.id,
          seed,
          visibility: 'private',
        });
        // 刷新列表：侧边栏/选择页立刻能看到新角色
        await Alpine.store('app').loadCharacters();
        navigate(`#/chat/${result.id}`);
      } catch (error) {
        showError(error.message || '随机创建失败');
      } finally {
        this.creatingRandom = false;
      }
    },

    // ── 入口三：空白创建（手动逐字段填写，背景故事自由编写）────
    startBlank() {
      this.mode = 'manual';
      this.selectedTemplate = null;
      this.profile = blankProfile();
      this.traitsText = '';
      this.validationIssues = [];
      this.profileValid = true;
      this.currentStep = 1;
      this.validate();
    },

    bigFiveLabel(key) {
      return {
        openness: '开放性',
        conscientiousness: '尽责性',
        extraversion: '外向性',
        agreeableness: '宜人性',
        neuroticism: '神经质',
      }[key] || key;
    },

    selectedTemplateIcon() {
      const template = this.templates.find(
        (item) => item.id === this.selectedTemplate);
      return template?.icon || (this.mode === 'manual' ? '📝' : '✨');
    },

    /** 特质文本框 → 数组（顿号/逗号/空格分隔）。 */
    syncTraitsFromText() {
      if (!this.profile.personality) this.profile.personality = {};
      this.profile.personality.traits = String(this.traitsText || '')
        .split(/[、,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    },

    isLocked(field) {
      return this.lockedFields.has(field);
    },

    toggleLock(field) {
      if (this.lockedFields.has(field)) {
        this.lockedFields.delete(field);
      } else {
        this.lockedFields.add(field);
      }
    },

    /** 随机化用的模板 id：空白创建时借回退模板的池子。 */
    templateForRandom() {
      return this.selectedTemplate || FALLBACK_TEMPLATE;
    },

    /**
     * 随机单个字段。
     *
     * @param {string} field  接口字段名（见 FIELD_PATHS）
     * @param {string} [subKey] 子键：big_five 的单个维度（openness 等），
     *        只随机该维、其余四维保持不变
     */
    async randomizeField(field, subKey = null) {
      if (this.isLocked(field)) return;
      try {
        const result = await api.previewRandomField({
          template_id: this.templateForRandom(),
          field,
          current_profile: this.profile,
        });
        if (subKey) {
          // 性格单维：只要这一个维度，避免"点一个五个都变"
          if (!this.profile.personality) this.profile.personality = {};
          if (!this.profile.personality.big_five) {
            this.profile.personality.big_five = {};
          }
          this.profile.personality.big_five[subKey] = result.value[subKey];
        } else {
          const path = FIELD_PATHS[field] || field;
          setNestedValue(this.profile, path, result.value);
        }
        await this.validate();
      } catch (error) {
        showError(error.message || '随机失败');
      }
    },

    async randomizeAll() {
      try {
        const result = await api.previewRandomAll({
          template_id: this.templateForRandom(),
          locked_fields: Array.from(this.lockedFields),
          current_profile: this.profile,
        });
        this.profile = result.profile;
        this.profile.visibility = this.profile.visibility
          || {is_public: false};
        this.traitsText = (this.profile.personality?.traits || []).join('、');
        await this.validate();
      } catch (error) {
        showError(error.message || '随机失败');
      }
    },

    async validate() {
      // 字段改动后实时校验，issues 列在配置卡片底部。
      // 不阻止继续编辑，只在「下一步」和「确认创建」处禁用。
      try {
        const result = await api.previewValidate({profile: this.profile});
        this.validationIssues = result.issues || [];
        this.profileValid = result.valid !== false;
      } catch (error) {
        // 校验端点异常不阻塞编辑
        console.warn('校验失败:', error.message);
      }
    },

    get canAdvance() {
      return Boolean(this.profile?.name?.trim()) && this.profileValid;
    },

    nextStep() {
      if (this.currentStep < this.steps.length - 1) {
        this.currentStep += 1;
      }
    },

    prevStep() {
      if (this.currentStep > 0) {
        this.currentStep -= 1;
      }
    },

    async create() {
      if (!this.canAdvance || this.submitting) return;
      this.submitting = true;
      try {
        this.syncTraitsFromText();
        let result;
        if (this.mode === 'manual') {
          // 空白创建：整份档案走手动创建（文档 1 第 13.3 节第三条路径）
          result = await api.createManual({profile: this.profile});
        } else {
          // 顶层 name / visibility 是权威字段，overrides 里的同名键只是占位
          // （优先级见文档 2 第 6.3 节）
          result = await api.createFromTemplate({
            template_id: this.selectedTemplate,
            name: this.profile.name,
            visibility: this.profile.visibility?.is_public
              ? 'public' : 'private',
            overrides: this.profile,
          });
        }
        // 刷新列表：侧边栏/选择页立刻能看到新角色
        await Alpine.store('app').loadCharacters();
        navigate(`#/chat/${result.id}`);
      } catch (error) {
        showError(error.message || '创建失败');
      } finally {
        this.submitting = false;
      }
    },
  };
}
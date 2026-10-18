// 状态面板组件（文档 3 第 9.6 节）。

import {api} from '../api.js';
import {wsManager} from '../ws.js';
import {getCurrentCharacterId, registerPage,
        routeMatches} from '../router.js';
import {formatValue} from '../utils/format.js';

export function statePanel() {
  return {
    internalExpanded: false,
    breathing: false,          // v1.1：显著变化时的呼吸动画开关
    character: null,
    wsBound: false,            // WS 处理器只绑一次（load() 会被重复触发）
    state: {expression: {}, internal: {}, affective: {}},

    expressionDimensions: [
      {key: 'PLE', label: '愉悦度', min: -1, max: 1},
      {key: 'ARO', label: '唤醒度', min: -1, max: 1},
      {key: 'DOM', label: '支配感', min: -1, max: 1},
      {key: 'INT', label: '亲密感', min: 0, max: 1},
      {key: 'SEC', label: '安全感', min: 0, max: 1},
    ],

    affectiveDimensions: [
      {key: 'AFF', label: '好感度'},
      {key: 'TRS', label: '信任度'},
      {key: 'ATT', label: '依恋度'},
    ],

    internalDimensions: [
      {key: 'ANX', label: '焦虑'},
      {key: 'LON', label: '孤独'},
      {key: 'CUR', label: '好奇'},
      {key: 'ANT', label: '期待'},
      {key: 'GUI', label: '愧疚'},
      {key: 'PRO', label: '保护欲'},
      {key: 'DEP', label: '依赖'},
      {key: 'PAT', label: '耐心'},
      {key: 'VUL', label: '脆弱'},
      {key: 'SAT', label: '满足'},
      {key: 'JEA', label: '嫉妒'},
      {key: 'TRU', label: '信任'},
    ],

    init() {
      registerPage(/^\/chat\//, () => this.load());
      if (routeMatches(/^\/chat\//)) this.load();
    },

    async load() {
      const characterId = getCurrentCharacterId();
      if (!characterId) return;
      try {
        this.character = await api.getCharacter(characterId);
        this.state = await api.getState(characterId);
      } catch (error) {
        console.warn('状态加载失败:', error.message);
        return;
      }

      // 监听 WebSocket 状态更新（只绑一次：load() 会被重复触发，
      // 每次进入都 bind 会让一次推送更新 N 次）
      if (!this.wsBound) {
        this.wsBound = true;
        wsManager.on('state_update', (data) => {
          if (data.character_id === this.character?.id) {
            this.state = {
              expression: data.expression,
              internal: data.internal,
              affective: data.affective,
            };
            // 呼吸动画的判据来自后端的 delta，不是前端自己相减
            if (Math.abs(data.delta?.expression?.PLE ?? 0) > 0.15) {
              this.breathe();
            }
          }
        });
      }
    },

    normalize(value, dim) {
      if (value === null || value === undefined) return 0;
      return ((value - dim.min) / (dim.max - dim.min)) * 100;
    },

    stageTitle() {
      const affective = this.state.affective || {};
      return `${affective.stage_label || ''} ${affective.stage_code || ''}`.trim();
    },

    formatValue,

    breathe() {
      // 一次性脉冲 1.0 → 1.02 → 1.0，600ms 后自动收回（文档 3 第 8.1 节）
      this.breathing = true;
      setTimeout(() => { this.breathing = false; }, 600);
    },
  };
}

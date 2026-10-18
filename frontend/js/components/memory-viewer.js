// 记忆浏览器组件（文档 3 第 6.8 节）。

import {api} from '../api.js';
import {registerPage, navigate, getCurrentCharacterId,
        routeMatches} from '../router.js';
import {showError} from '../utils/toast.js';
import {formatRelative} from '../utils/format.js';

const EMOTION_TAGS = [
  {value: '', label: '全部标签'},
  {value: 'affectionate', label: '亲昵'},
  {value: 'playful', label: '俏皮'},
  {value: 'vulnerable', label: '脆弱'},
  {value: 'reassuring', label: '安抚'},
  {value: 'curious', label: '好奇'},
  {value: 'neutral', label: '中性'},
  {value: 'conflict', label: '冲突'},
  {value: 'hostile', label: '敌意'},
  {value: 'withdrawn', label: '退缩'},
  {value: 'proactive', label: '主动消息'},
];

const TIME_RANGES = [
  {value: '', label: '全部时间'},
  {value: 'last_7_days', label: '最近 7 天'},
  {value: 'last_30_days', label: '最近 30 天'},
];

export function memoryViewerPage() {
  return {
    characterId: null,
    characterName: '',
    query: '',
    emotionTag: '',
    timeRange: '',
    memories: [],
    loading: true,
    searching: false,

    emotionTags: EMOTION_TAGS,
    timeRanges: TIME_RANGES,

    init() {
      registerPage('/memory', () => this.load());
      if (routeMatches('/memory')) this.load();
    },

    async load() {
      this.characterId = getCurrentCharacterId()
        || Alpine.store('app').currentCharacter?.id;
      if (!this.characterId) {
        navigate('#/characters');
        return;
      }
      try {
        const character = await api.getCharacter(this.characterId);
        this.characterName = character.name || '';
      } catch (error) {
        showError(error.message || '角色加载失败');
        return;
      }
      await this.search();
    },

    async search() {
      if (!this.query.trim()) {
        this.memories = [];
        this.loading = false;
        return;
      }
      this.searching = true;
      try {
        const params = {
          character_id: this.characterId,
          q: this.query.trim(),
          limit: 50,
        };
        // 过滤参数：emotion_tag / time_range 由后端应用层过滤（文档 3 第 6.8 节）
        if (this.emotionTag) params.emotion_tag = this.emotionTag;
        if (this.timeRange) params.time_range = this.timeRange;
        const result = await api.searchMemory(params);
        this.memories = result.memories || [];
      } catch (error) {
        showError(error.message || '检索失败');
      } finally {
        this.searching = false;
        this.loading = false;
      }
    },

    tagClass(tag) {
      return `memory-card__tag--${tag || 'neutral'}`;
    },

    intensityOf(memory) {
      return memory.intensity === null || memory.intensity === undefined
        ? '—' : Number(memory.intensity).toFixed(2);
    },

    importanceOf(memory) {
      return memory.importance === null || memory.importance === undefined
        ? '—' : Number(memory.importance).toFixed(2);
    },

    triggerOf(memory) {
      return memory.trigger_reason || memory.metadata?.trigger_reason || '';
    },

    back() {
      // 直接打开 #/memory 时 history 可能是空的，回聊天页更可靠
      const characterId = this.characterId
        || Alpine.store('app').currentCharacter?.id;
      navigate(characterId ? `#/chat/${characterId}` : '#/characters');
    },

    formatRelative,
  };
}

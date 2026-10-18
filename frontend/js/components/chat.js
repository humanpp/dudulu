// 聊天页组件（文档 3 第 9.5 节）。

import {api} from '../api.js';
import {wsManager} from '../ws.js';
import {sendMessageStream} from '../sse.js';
import {generateId} from '../utils/id.js';
import {showError, showSuccess} from '../utils/toast.js';
import {renderMarkdown} from '../utils/markdown.js';
import {formatTimestamp} from '../utils/format.js';
import {getCurrentCharacterId, navigate, registerPage,
        routeMatches} from '../router.js';

// 长按复制的触发时长（ms）与左滑露出删除的阈值（px）
const LONG_PRESS_MS = 500;
const SWIPE_REVEAL_PX = 56;

export function chatPage() {
  return {
    messages: [],
    input: '',
    isSending: false,
    isStreaming: false,
    streamingContent: '',
    character: null,
    sessionId: null,
    failedMessage: null,   // 发送失败待重试的内容（文档 3 第 7.6 节）
    enterSeq: 0,           // enter() 代数锁（并发进入作废旧链）
    // 重新生成（文档 3 第 7.2 节：输出中断时）
    lastUserMessage: null,
    // 左滑删除 + 长按复制（文档 3 第 10.3 节）
    swipedMessageId: null,
    _touch: null,          // {messageId, startX, startY, timer, fired}

    init() {
      registerPage(/^\/chat\//, () => this.enter());
      // WS 处理器只绑一次：enter() 会被重复触发（路由切换/刷新），
      // 每次进入都 bind 会让一条主动消息弹 N 次（2026-10-17）
      wsManager.on('proactive_message', (data) => {
        if (data.character_id === this.character?.id) {
          this.messages.push({
            id: generateId(),
            role: 'assistant',
            content: data.message,
            timestamp: data.timestamp,
            isProactive: true,
          });
          this.scrollToBottom(true);
        }
      });
      // 打字指示器出现时也滚到底（它在 isSending 置真后渲染）
      this.$watch('isSending', (value) => {
        if (value) this.scrollToBottom(true);
      });
      // 隐藏页面（x-show）也会执行 init：仅在当前路由匹配时才加载，
      // 否则等路由切换时由初始化器触发（避免启动时乱跳路由）
      if (routeMatches(/^\/chat\//)) this.enter();
    },

    async enter() {
      const store = Alpine.store('app');
      const characterId = getCurrentCharacterId();
      if (!characterId) {
        window.location.hash = '#/characters';
        return;
      }
      // 代数锁：路由切换/重复触发会产生并发 enter，
      // 旧链在 await 之后发现已被取代就安静退出（2026-10-17：
      // 用户在 enter() 未完成时按 Enter，character 还是 null → send 崩）
      const seq = ++this.enterSeq;
      try {
        this.character = await api.getCharacter(characterId);
        if (seq !== this.enterSeq) return;
        store.setCurrentCharacter(this.character);
      } catch (error) {
        if (seq !== this.enterSeq) return;
        showError(error.message || '角色加载失败');
        return;
      }

      try {
        const session = await api.createSession(characterId);
        if (seq !== this.enterSeq) return;
        this.sessionId = session.session_id;
      } catch (error) {
        if (seq !== this.enterSeq) return;
        showError(error.message || '会话创建失败');
        return;
      }

      try {
        const result = await api.getRecentMessages(characterId);
        if (seq !== this.enterSeq) return;
        // 归一化：snake_case → camelCase（裁决②）
        this.messages = (result.messages || []).map(normalizeMessage);
      } catch (error) {
        if (seq !== this.enterSeq) return;
        showError(error.message || '历史消息加载失败');
        return;
      }

      store.messages = this.messages;
      // 进入聊天页即清除该角色的红点（文档 3 第 7.4 节）
      store.clearProactiveUnread(characterId);
      wsManager.connect();
      this.scrollToBottom();
    },

    openCharacters() {
      navigate('#/characters');
    },

    openEdit() {
      navigate(`#/character/${this.character.id}/edit`);
    },

    openMemory() {
      navigate('#/memory');
    },

    async send() {
      if (!this.input.trim() || this.isSending) return;
      // 页面还在加载（enter() 未完成）：不发送、不清空输入、
      // 不进气泡——否则 this.character.id 抛 TypeError，
      // 消息卡在 'sending' 永无回复（2026-10-17 实测）
      if (!this.character || !this.sessionId) {
        showError('页面还在加载，稍等一下再发');
        return;
      }

      const content = this.input.trim();
      const userMessage = {
        id: generateId(),
        role: 'user',
        content,
        timestamp: new Date().toISOString(),
        status: 'sending',
      };
      this.messages.push(userMessage);
      this.input = '';
      this.lastUserMessage = content;
      // 发送后把输入框高度收回去（不清的话下一行还是老高度）
      this.$nextTick(() => {
        if (this.$refs.input) this.$refs.input.style.height = '';
      });
      this.isSending = true;
      this.isStreaming = true;
      this.streamingContent = '';
      this.swipedMessageId = null;
      this.scrollToBottom(true);

      await sendMessageStream({
        characterId: this.character.id,
        sessionId: this.sessionId,
        message: content,
        onToken: (token) => {
          this.streamingContent += token;
          this.scrollToBottom();
        },
        onDone: (data) => {
          this.messages.push({
            id: generateId(),
            role: 'assistant',
            content: this.streamingContent,
            timestamp: new Date().toISOString(),
          });
          Alpine.store('app').state = data.state;
          userMessage.status = 'sent';
          this.isStreaming = false;
          this.streamingContent = '';
          this.isSending = false;
          this.failedMessage = null;
          this.scrollToBottom(true);
        },
        onError: (error) => {
          // 气泡标红 + 重试（文档 3 第 7.1 / 7.6 节）
          userMessage.status = 'failed';
          this.isSending = false;
          this.isStreaming = false;
          this.failedMessage = content;
          showError(error.message || '发送失败');
        },
      });
    },

    retry() {
      const content = this.failedMessage;
      if (!content) return;
      // 移除失败的用户气泡后重发
      this.messages = this.messages.filter(
        (message) => message.status !== 'failed');
      this.failedMessage = null;
      this.input = content;
      this.send();
    },

    /** 重新生成：去掉最后一条助手回复，重发最后一条用户消息。 */
    async regenerate() {
      if (this.isSending || !this.lastUserMessage || !this.character) return;
      // 找最后一条用户消息；连它一起截掉——send() 会重新加这条用户气泡，
      // 保留的话会重复（2026-10-18 自查发现）
      let lastUserIndex = -1;
      this.messages.forEach((message, index) => {
        if (message.role === 'user') lastUserIndex = index;
      });
      if (lastUserIndex === -1) return;
      this.messages = this.messages.slice(0, lastUserIndex);
      this.failedMessage = null;
      this.input = this.lastUserMessage;
      await this.send();
    },

    get canRegenerate() {
      return !this.isSending && !this.isStreaming
        && this.lastUserMessage && this.messages.some(
          (message) => message.role === 'assistant');
    },

    // ── 左滑删除 + 长按复制（文档 3 第 10.3 节）────────────

    onMessageTouchStart(message, event) {
      const point = event.touches?.[0] || event;
      this._touch = {
        messageId: message.id,
        startX: point.clientX,
        startY: point.clientY,
        fired: false,
        timer: setTimeout(() => {
          if (this._touch && !this._touch.fired) {
            this._touch.fired = true;
            this.copyMessage(message);
          }
        }, LONG_PRESS_MS),
      };
    },

    onMessageTouchMove(event) {
      const touch = this._touch;
      if (!touch) return;
      const point = event.touches?.[0] || event;
      const dx = point.clientX - touch.startX;
      const dy = point.clientY - touch.startY;
      // 判定为横向滑动：取消长按，越过阈值露出删除按钮
      if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
        clearTimeout(touch.timer);
        this.swipedMessageId = dx < -SWIPE_REVEAL_PX ? touch.messageId : null;
      }
    },

    onMessageTouchEnd() {
      this._clearTouchTimer();
    },

    _clearTouchTimer() {
      if (this._touch?.timer) clearTimeout(this._touch.timer);
      this._touch = null;
    },

    async copyMessage(message) {
      try {
        await navigator.clipboard.writeText(message.content || '');
        showSuccess('已复制');
      } catch (error) {
        showError('复制失败');
      }
    },

    deleteMessage(message) {
      this.messages = this.messages.filter((item) => item.id !== message.id);
      this.swipedMessageId = null;
      showSuccess('已从当前视图移除');
    },

    onEnter(event) {
      // Shift+Enter 交给浏览器默认行为（换行）；单独按 Enter 才发送。
      // 不能在模板里写 @keydown.enter.prevent，否则 Shift+Enter 的换行也被拦掉。
      if (event.shiftKey) return;
      event.preventDefault();
      this.send();
    },

    /**
     * 滚动到底部；但用户自己往上翻看历史时不强拽（流式输出期间
     * 每个 token 都会调它——2026-10-18 体验问题）。
     */
    scrollToBottom(force = false) {
      this.$nextTick(() => {
        const container = this.$refs.messages;
        if (!container) return;
        const distance = container.scrollHeight - container.scrollTop
          - container.clientHeight;
        if (force || distance < 80) {
          container.scrollTop = container.scrollHeight;
        }
      });
    },

    renderMarkdown,
    formatTimestamp,
  };
}

/** 后端消息行 → 前端消息对象（snake_case → camelCase）。 */
function normalizeMessage(row) {
  return {
    id: row.id,
    role: row.role,
    content: row.content,
    timestamp: row.timestamp,
    isProactive: Boolean(row.is_proactive),
    emotionTag: row.emotion_tag || null,
  };
}

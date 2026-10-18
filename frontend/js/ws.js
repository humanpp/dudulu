// WebSocket 管理（文档 3 第 9.4 节）。
// 认证握手、断线指数退避重连、state_update / proactive_message 分发。

class WebSocketManager {
  constructor() {
    this.ws = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.handlers = new Map();
    // 主动断开（退出登录）：不再自动重连
    this.manuallyClosed = false;
  }

  connect() {
    const token = Alpine.store('app').token;
    if (!token) return;

    // 防重复连接：已在 CONNECTING/OPEN 就复用。
    // 聊天页、外壳、状态面板都会调 connect()——不拦的话会建多条连接，
    // 且旧 socket 的 onopen 里 this.ws 已指向新 socket，往 CONNECTING
    // 里的连接 send 会抛 DOMException（2026-10-17 实测）。
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING
                    || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 局部变量：闭包捕获的是这条连接自己，避免被后续 connect() 顶掉后
    // 往新连接（仍 CONNECTING）send
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/ws/events`);
    this.ws = socket;
    this.manuallyClosed = false;

    socket.onopen = () => {
      socket.send(JSON.stringify({type: 'auth', token}));
      this.reconnectAttempts = 0;
    };

    socket.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (error) {
        return; // 非 JSON 帧忽略
      }

      if (data.type === 'state_update') {
        // 后端推的是完整状态 + delta（文档 2 第 6.11 节），只取三层，
        // 不把 type / character_id / delta 混进 state 对象
        const store = Alpine.store('app');
        if (data.character_id === store.currentCharacter?.id) {
          store.state = {
            expression: data.expression,
            internal: data.internal,
            affective: data.affective,
          };
          // 呼吸动画的判据，别丢掉（文档 3 第 7.3 节第 4 条）
          store.lastDelta = data.delta || null;
        }
        this._emit('state_update', data);
      } else if (data.type === 'proactive_message') {
        // 不是当前角色：只打未读红点，不往聊天区塞消息
        const store = Alpine.store('app');
        if (data.character_id !== store.currentCharacter?.id) {
          store.markProactiveUnread(data.character_id);
        }
        this._emit('proactive_message', data);
      }
    };

    socket.onclose = () => {
      if (this.manuallyClosed) return;
      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        const delay = Math.pow(2, this.reconnectAttempts) * 1000;
        setTimeout(() => {
          this.reconnectAttempts += 1;
          this.connect();
        }, delay);
      }
    };
  }

  on(event, handler) {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, []);
    }
    this.handlers.get(event).push(handler);
  }

  _emit(event, data) {
    for (const handler of this.handlers.get(event) || []) {
      handler(data);
    }
  }

  disconnect() {
    this.manuallyClosed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

export const wsManager = new WebSocketManager();

// 应用外壳组件：侧边栏 + 顶栏 + 状态面板容器（文档 3 第 6.1 节布局系统）。
// 角色列表与未读红点统一读 store（单一数据源），本地不维护副本。

import {wsManager} from '../ws.js';
import {navigate} from '../router.js';
import {characterAvatar} from '../theme.js';

export function appShell() {
  return {
    route: '/characters',
    sidebarOpen: false,
    statePanelOpen: false,
    // 网络断开横幅（文档 3 第 7.6 节）
    offline: false,

    init() {
      const store = Alpine.store('app');
      // 跟随 store 的路由状态
      this.$watch('$store.app.route', (value) => {
        this.route = value;
      });
      this.route = store.route || '/characters';

      // 离线检测：横幅 + 恢复联网时重连 WS
      this.offline = !navigator.onLine;
      window.addEventListener('offline', () => { this.offline = true; });
      window.addEventListener('online', () => {
        this.offline = false;
        if (store.token) wsManager.connect();
      });

      if (store.token) {
        store.loadCharacters();
        wsManager.connect();
        wsManager.on('proactive_message', () => {
          // 红点数据在 store.unreadProactive，这里只为触发重渲染读一次
          this.unreadSnapshot = {...store.unreadProactive};
        });
      }
    },

    // 未读红点的快照（store 是响应式的，这里取一次驱动渲染）
    unreadSnapshot: {},

    get totalUnread() {
      const store = Alpine.store('app');
      return Object.values(store.unreadProactive)
        .reduce((sum, count) => sum + count, 0);
    },

    avatarOf(character) {
      return characterAvatar(character);
    },

    isActiveChat(characterId) {
      return this.route === `/chat/${characterId}`;
    },

    isLoggedIn() {
      return Boolean(Alpine.store('app').token);
    },

    openChat(characterId) {
      this.sidebarOpen = false;
      navigate(`#/chat/${characterId}`);
    },

    openCharacters() {
      this.sidebarOpen = false;
      navigate('#/characters');
    },

    openCreate() {
      this.sidebarOpen = false;
      navigate('#/character/create');
    },

    openSettings() {
      this.sidebarOpen = false;
      navigate('#/settings');
    },

    openMemory() {
      navigate('#/memory');
    },

    toggleStatePanel() {
      this.statePanelOpen = !this.statePanelOpen;
    },

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
  };
}

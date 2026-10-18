// 登录/注册页组件（文档 3 第 6.2 节）。

import {api} from '../api.js';
import {showError} from '../utils/toast.js';

export function authPage() {
  return {
    mode: 'login',       // login | register
    username: '',
    password: '',
    error: '',
    loading: false,

    init() {
      // 已登录访问登录页：路由守卫会跳走，这里双保险
      if (Alpine.store('app').token) {
        window.location.hash = '#/characters';
      }
    },

    get isLogin() {
      return this.mode === 'login';
    },

    get submitLabel() {
      return this.loading
        ? (this.isLogin ? '登录中…' : '注册中…')
        : (this.isLogin ? '登 录' : '注 册');
    },

    toggleMode() {
      this.mode = this.isLogin ? 'register' : 'login';
      this.error = '';
    },

    async submit() {
      if (!this.username.trim() || !this.password || this.loading) return;
      this.error = '';
      this.loading = true;
      try {
        const data = {username: this.username.trim(), password: this.password};
        const result = this.isLogin
          ? await api.login(data)
          : await api.register(data);
        // 文档 2 第 6.1 节：响应字段是 {user_id, token}（不是 access_token）
        const store = Alpine.store('app');
        store.setToken(result.token);
        // 设置页要显示用户名：登录/注册响应只有 user_id，用户名取自输入框
        store.user = {id: result.user_id, username: this.username.trim()};
        window.location.hash = '#/characters';
      } catch (error) {
        this.error = error.message || '操作失败，请重试';
      } finally {
        this.loading = false;
      }
    },
  };
}

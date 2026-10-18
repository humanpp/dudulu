// hash 路由（文档 3 第 4 章 router.js，v1.1：导出 getCurrentCharacterId）。
// 路由表 + 页面初始化器注册；hashchange 时切换并调用对应初始化器。

// 路由模式 → 页面初始化器（组件在 init 时注册，见 app.js）
const pageInitializers = new Map();

export function registerPage(pattern, initFn) {
  pageInitializers.set(pattern, initFn);
}

/** 当前 hash（#/chat/x → '/chat/x'；空 hash 视为角色选择页）。 */
export function currentRoute() {
  const hash = window.location.hash.replace(/^#/, '');
  return hash || '/characters';
}

/** 当前路由对应的角色 id（#/chat/{id} 与 #/character/{id}/edit）。 */
export function getCurrentCharacterId() {
  const chat = currentRoute().match(/^\/chat\/([^/?]+)/);
  if (chat) return decodeURIComponent(chat[1]);
  const edit = currentRoute().match(/^\/character\/([^/]+)\/edit/);
  if (edit) return decodeURIComponent(edit[1]);
  return null;
}

/**
 * 当前路由是否匹配某模式（页面组件 init 时用：隐藏页面不自动加载，
 * 等路由切换时经 registerPage 的初始化器触发，避免启动时乱跳路由）。
 */
export function routeMatches(pattern) {
  const route = currentRoute();
  return pattern instanceof RegExp ? pattern.test(route) : pattern === route;
}

function matchInitializers(route) {
  // 跑**所有**匹配的初始化器：聊天页与状态面板都注册了 /chat/ 模式，
  // 只跑第一个会让另一个不刷新（2026-10-17：切角色后状态面板不更新）
  const matched = [];
  for (const [pattern, initFn] of pageInitializers) {
    if (pattern instanceof RegExp ? pattern.test(route) : pattern === route) {
      matched.push(initFn);
    }
  }
  return matched;
}

let started = false;

// 已知路由（未知的一律回角色页，避免主区域一片空白）
const KNOWN_ROUTES = [
  /^\/login$/,
  /^\/characters$/,
  /^\/character\/create$/,
  /^\/character\/[^/]+\/edit$/,
  /^\/chat\/[^/]+$/,
  /^\/memory$/,
  /^\/settings$/,
];

function isKnownRoute(route) {
  return KNOWN_ROUTES.some((pattern) => pattern.test(route));
}

/**
 * 启动路由：监听 hashchange，路由变化时更新 store 并调用页面初始化器。
 * 认证守卫：无 token 且不在登录页 → 跳登录；已登录访问登录页 → 跳角色页；
 * 未知路由 → 回角色页。
 */
export function startRouter() {
  if (started) return;
  started = true;

  const apply = () => {
    const route = currentRoute();
    const store = Alpine.store('app');
    store.route = route;
    store.routeSeq = (store.routeSeq || 0) + 1;
    if (!store.ui) store.ui = {sidebarOpen: false, statePanelOpen: false};
    store.ui.sidebarOpen = false;

    // 认证守卫
    if (!store.token && route !== '/login') {
      window.location.hash = '#/login';
      return;
    }
    if (store.token && route === '/login') {
      window.location.hash = '#/characters';
      return;
    }
    if (!isKnownRoute(route)) {
      window.location.hash = '#/characters';
      return;
    }

    const initializers = matchInitializers(route);
    for (const initializer of initializers) initializer(route);
  };

  window.addEventListener('hashchange', apply);
  apply();
}

/** 页面跳转。 */
export function navigate(hash) {
  window.location.hash = hash;
}

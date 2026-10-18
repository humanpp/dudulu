// 入口：Alpine 初始化（文档 3 第 4 章 app.js）。
//
// ⚠ 加载顺序契约（2026-10-17 事故修复）：
// vendor 构建结尾是 `window.Alpine=...; queueMicrotask(()=>start())`——
// 它不等 DOMContentLoaded，微任务里立即启动。所以：
//   1. index.html 里本脚本必须排在 vendor Alpine **之前**
//      （deferred 脚本按文档顺序执行，app.js 先跑）；
//   2. 组件注册放在 `alpine:init` 监听里——Alpine 在 start() 开头、
//      遍历 DOM 之前派发该事件，此时注册的组件才会被 x-data 找到。
// 顺序反了 / 直接顶层注册，都会全线报 "xxx is not defined" 白屏。
// 不能用 `import Alpine from 'alpinejs'`：浏览器没有 import map，
// 解析不了裸模块名，整条模块图加载失败（另一宗白屏事故）。

import {initStore} from './store.js';
import {appShell} from './components/shell.js';
import {authPage} from './components/auth.js';
import {chatPage} from './components/chat.js';
import {statePanel} from './components/state-panel.js';
import {characterSelectPage} from './components/character-select.js';
import {characterCreatePage} from './components/character-create.js';
import {characterEditPage} from './components/character-edit.js';
import {memoryViewerPage} from './components/memory-viewer.js';
import {settingsPage} from './components/settings.js';
import {startRouter} from './router.js';

function registerAll(alpine) {
  initStore();

  alpine.data('appShell', appShell);
  alpine.data('authPage', authPage);
  alpine.data('chatPage', chatPage);
  alpine.data('statePanel', statePanel);
  alpine.data('characterSelectPage', characterSelectPage);
  alpine.data('characterCreatePage', characterCreatePage);
  alpine.data('characterEditPage', characterEditPage);
  alpine.data('memoryViewerPage', memoryViewerPage);
  alpine.data('settingsPage', settingsPage);

  startRouter();
}

function showVendorError() {
  // vendor 脚本没加载上（文件缺失/路径错）：给出可见的错误提示，
  // 而不是留一片空白让用户猜
  document.body.innerHTML =
    '<div style="font-family: system-ui; padding: 48px; text-align: center">' +
    '<h2>前端依赖加载失败</h2>' +
    '<p>assets/vendor/alpine.min.js 未加载。请确认该文件存在，' +
    '并强制刷新（Ctrl+F5）。</p></div>';
}

if (window.Alpine) {
  // Alpine 已就绪（顺序变化/缓存场景）：直接注册
  registerAll(window.Alpine);
} else {
  // 主路径：等 Alpine start() 时派发的 alpine:init（派发在 document 上）
  document.addEventListener('alpine:init', () => {
    if (window.Alpine) {
      registerAll(window.Alpine);
    } else {
      showVendorError();
      throw new Error('alpine:init 已派发但 window.Alpine 未定义');
    }
  });

  // 兜底：万一 Alpine 始终没出现（vendor 404/被拦），5 秒后给可见提示，
  // 避免用户对着一片空白猜
  setTimeout(() => {
    if (!window.Alpine) showVendorError();
  }, 5000);
}

// 布局自诊断：访问 /?debug=1 时在 Console 打出主区域与各页面的
// 实际高度/溢出，用来判断"没滚动条"到底是高度没约束还是样式没加载
if ((window.location.search || '').includes('debug=1')) {
  setTimeout(() => {
    const main = document.querySelector('.main-area');
    const mainStyle = main ? getComputedStyle(main) : null;
    console.log('[debug] main-area clientHeight =', main?.clientHeight,
                '| height =', mainStyle?.height,
                '| overflow =', mainStyle?.overflow,
                '| display =', mainStyle?.display);
    const pages = [...document.querySelectorAll('.main-area > div')];
    console.table(pages.map((page) => ({
      class: page.className.split(' ')[1] || page.className,
      display: getComputedStyle(page).display,
      clientHeight: page.clientHeight,
      scrollHeight: page.scrollHeight,
      overflowY: getComputedStyle(page).overflowY,
      scrollable: page.scrollHeight > page.clientHeight,
    })));
  }, 1500);
}

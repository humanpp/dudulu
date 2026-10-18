// 模块图冒烟：在 Node 里用 DOM 桩真实 import app.js，
// 验证整条 import 链能解析、顶层求值不抛错（浏览器同款 ESM 语义），
// 并模拟浏览器的脚本时序（2026-10-17 事故的回归保障）：
//   1. app.js 先执行（此时 window.Alpine 未定义）→ 注册 alpine:init 监听
//   2. vendor Alpine 后执行 → window.Alpine 出现
//   3. Alpine.start() → 派发 alpine:init → 组件注册 → 遍历 DOM
// 用法：node check-modules.mjs

const registered = new Map();
const store = {};
const alpineInitListeners = [];

const fakeAlpine = {
  store(name, value) {
    // 真 Alpine：单参 = 读取已注册 store；双参 = 注册
    if (value === undefined) return store[name];
    store[name] = typeof value === 'function' ? value() : value;
  },
  data(name, factory) {
    registered.set(name, factory);
  },
};

// 浏览器初始状态：Alpine 还没加载（app.js 先于 vendor 执行）
globalThis.window = {
  Alpine: undefined,
  location: {hash: '', search: '', protocol: 'http:',
             host: 'localhost:8000'},
  addEventListener() {},
  localStorage: {
    getItem: () => null,
    setItem() {},
    removeItem() {},
  },
};
globalThis.document = {
  documentElement: {style: {setProperty() {}, removeProperty() {}}},
  body: {innerHTML: ''},
  querySelector: () => null,
  createElement: () => ({style: {}, classList: {add() {}, remove() {}}}),
  // 捕获 alpine:init 监听（真 Alpine 在 start() 时派发到 document）
  addEventListener(type, handler) {
    if (type === 'alpine:init') alpineInitListeners.push(handler);
  },
};
globalThis.localStorage = globalThis.window.localStorage;
globalThis.Alpine = undefined;

await import('./js/app.js');

// ── 断言 1：app.js 执行后只注册监听，不直接注册组件 ──
if (registered.size !== 0) {
  console.error('FAIL: app.js 在 Alpine 未就绪时就注册了组件（时序错误）');
  process.exit(1);
}
if (alpineInitListeners.length !== 1) {
  console.error('FAIL: alpine:init 监听未注册');
  process.exit(1);
}

// ── 步骤 2/3：vendor Alpine 到位 → start() → 派发 alpine:init ──
globalThis.window.Alpine = fakeAlpine;
globalThis.Alpine = fakeAlpine;
alpineInitListeners.forEach((handler) => handler());

const expected = ['appShell', 'authPage', 'chatPage', 'statePanel',
  'characterSelectPage', 'characterCreatePage', 'characterEditPage',
  'memoryViewerPage', 'settingsPage'];
const missing = expected.filter((name) => !registered.has(name));

console.log('Alpine.data 注册:', registered.size, '个');
console.log('store.app 初始化:', Boolean(store.app), '| token:',
  store.app?.token ?? '(null)');
console.log('路由状态: route =', store.app?.route);

if (missing.length) {
  console.error('未注册的组件:', missing);
  process.exit(1);
}
if (!store.app) {
  console.error('store.app 未初始化');
  process.exit(1);
}
console.log('模块图冒烟通过：import 链完整，时序契约正确（alpine:init 注册）');

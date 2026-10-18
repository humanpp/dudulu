// 组件级冒烟：用 DOM/Alpine/fetch/WebSocket 桩在 Node 里真实驱动组件，
// 覆盖 2026-10-17 三个实测事故的回归：
//   ① send() 在 enter() 未完成时抛 TypeError（character 为 null）
//   ② 重复 connect() 往 CONNECTING 的连接 send 认证帧（DOMException）
//   ③ WS 处理器重复绑定（来回导航后一条消息更新/弹 N 次）
// 用法：node check-components.mjs

import assert from 'node:assert';

// ── Alpine/store 桩 ────────────────────────────────────────────
const store = {};
const fakeAlpine = {
  store(name, value) {
    if (value === undefined) return store[name];
    store[name] = typeof value === 'function' ? value() : value;
  },
  data() {},
};
globalThis.Alpine = fakeAlpine;
globalThis.window = {
  Alpine: fakeAlpine,
  location: {hash: '#/chat/test-char', protocol: 'http:',
             host: 'localhost:8000'},
  addEventListener() {},
  localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
};
globalThis.document = {
  documentElement: {style: {setProperty() {}, removeProperty() {}}},
  body: {appendChild() {}, innerHTML: ''},
  querySelector: () => null,
  createElement: () => ({
    style: {}, textContent: '',
    classList: {add() {}, remove() {}},
    setAttribute() {},
    appendChild() {},
    remove() {},
  }),
  addEventListener() {},
};
globalThis.localStorage = globalThis.window.localStorage;

// ── fetch 桩 ───────────────────────────────────────────────────
const requests = [];
const encoder = new TextEncoder();

function jsonResponse(data) {
  return {ok: true, status: 200, json: async () => data};
}

function sseResponse(frames) {
  const payload = frames.map(
    (f) => `event: ${f.event}\ndata: ${f.data}\n\n`).join('');
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(payload));
      controller.close();
    },
  });
  return {ok: true, status: 200, body: {getReader: () => stream.getReader()}};
}

globalThis.fetch = async (url, options = {}) => {
  const path = String(url);
  requests.push({path, method: options.method || 'GET',
                 body: options.body ? JSON.parse(options.body) : null});
  if (path.includes('/api/chat/stream')) {
    return sseResponse([
      {event: 'token', data: '你'},
      {event: 'token', data: '好呀'},
      {event: 'done', data: JSON.stringify({state: {affective: {}}})},
    ]);
  }
  if (path.includes('/api/character/preview/random-field')) {
    const field = JSON.parse(options.body).field;
    const values = {
      name: '随机名',
      body_type: 'athletic',
      occupation: '摄影师',
      age: 30,
      big_five: {openness: 0.9, conscientiousness: 0.1, extraversion: 0.1,
                 agreeableness: 0.1, neuroticism: 0.1},
    };
    return jsonResponse({field, value: values[field]});
  }
  if (path.includes('/api/character/preview/validate')) {
    return jsonResponse({valid: true, issues: []});
  }
  if (path.includes('/api/session/create')) {
    return jsonResponse({session_id: 'sess-1'});
  }
  if (path.includes('/api/messages/recent')) {
    return jsonResponse({messages: []});
  }
  if (path.includes('/api/state')) {
    return jsonResponse({expression: {}, internal: {}, affective: {}});
  }
  if (path.includes('/api/character/list')) {
    return jsonResponse({characters: [{id: 'c1', name: '侧边栏角色'}]});
  }
  if (path.includes('/api/character/create/template')) {
    return jsonResponse({id: 'new-char', name: '新角色'});
  }
  if (path.includes('/api/character/')) {
    return jsonResponse({id: 'test-char', name: '知夏', nickname: '知夏'});
  }
  return jsonResponse({});
};

// ── WebSocket 桩（模拟 CONNECTING/OPEN）────────────────────────
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(data) {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error('Still in CONNECTING state');
    }
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
}
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.OPEN = 1;
FakeWebSocket.CLOSING = 2;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];
globalThis.WebSocket = FakeWebSocket;

// ── 初始化 store（token 就绪）─────────────────────────────────
const {initStore} = await import('./js/store.js');
initStore();
Alpine.store('app').token = 'test-token';

// ── 用例 ①：enter() 未完成时 send() ───────────────────────────
const {chatPage} = await import('./js/components/chat.js');
const chat = chatPage();
// Alpine 注入的 magic（组件工厂本身不带，测试补桩）
chat.$nextTick = (fn) => fn();
chat.$refs = {};
chat.input = '你好';

const entering = chat.enter();           // 不 await：模拟进行中
let threw = null;
try {
  await chat.send();
} catch (error) {
  threw = error;
}
assert.equal(threw, null, '① send() 在 enter() 未完成时不应抛错');
assert.equal(chat.messages.length, 0, '① 未就绪不应进气泡');
assert.equal(chat.input, '你好', '① 未就绪不应清空输入');

await entering;                          // 等 enter() 完成
assert.equal(chat.character?.id, 'test-char', '① enter() 完成后角色就绪');

// ── 用例 ②：就绪后发送走 SSE，characterId 正确 ────────────────
requests.length = 0;
await chat.send();
const streamRequest = requests.find((r) => r.path.includes('/api/chat/stream'));
assert.ok(streamRequest, '② 应发出流式请求');
assert.equal(streamRequest.body.character_id, 'test-char',
             '② characterId 应为当前角色');
assert.equal(chat.messages.length, 2, '② 用户+助手两条气泡');
assert.equal(chat.messages[1].content, '你好呀', '② 助手内容来自 token 拼接');

// ── 用例 ③：重复 connect 只建一条连接，认证发在正确的 socket ──
const {wsManager} = await import('./js/ws.js');
wsManager.connect();
wsManager.connect();                     // 第二次应复用
assert.equal(FakeWebSocket.instances.length, 1,
             '③ 重复 connect() 不应建第二条连接');
const socket = FakeWebSocket.instances[0];
socket.readyState = FakeWebSocket.OPEN;  // 模拟连接就绪
socket.onopen();
assert.equal(socket.sent.length, 1, '③ 认证帧应发在连接就绪后');
assert.deepEqual(JSON.parse(socket.sent[0]),
                 {type: 'auth', token: 'test-token'}, '③ 认证帧内容');

// ── 用例 ④：WS 处理器只绑一次 ────────────────────────────────
const {statePanel} = await import('./js/components/state-panel.js');
const panel = statePanel();
await panel.load();                      // 第一次：绑处理器
await panel.load();                      // 第二次：不应重复绑
const before = FakeWebSocket.instances.length;
socket.onmessage({data: JSON.stringify({
  type: 'state_update', character_id: 'test-char',
  expression: {PLE: 0.5}, internal: {}, affective: {},
  delta: {expression: {PLE: 0.5}},
})});
assert.equal(panel.state.expression.PLE, 0.5, '④ 推送应更新面板');
assert.equal(FakeWebSocket.instances.length, before,
             '④ 不应因重复 load 建新连接');

// ── 用例 ⑤：向导 🎲 的字段路径映射（2026-10-18 四个反馈）──
const {characterCreatePage} = await import(
  './js/components/character-create.js');
const wizard = characterCreatePage();
wizard.$nextTick = (fn) => fn();
wizard.profile = {
  name: '', gender: 'female', age: 22,
  appearance: {body_type: 'slim'},
  personality: {big_five: {openness: 0.5, conscientiousness: 0.5,
                           extraversion: 0.5, agreeableness: 0.5,
                           neuroticism: 0.5}, traits: []},
  background: {occupation: '', current_state: ''},
  visibility: {is_public: false},
};

// 名字：按性别随机
await wizard.randomizeField('name');
assert.equal(wizard.profile.name, '随机名', '⑤ 名字随机未写入 profile.name');

// 体型：写入 appearance.body_type（不是顶层 body_type）
await wizard.randomizeField('body_type');
assert.equal(wizard.profile.appearance.body_type, 'athletic',
             '⑤ 体型随机未写入 appearance.body_type');
assert.equal(wizard.profile.body_type, undefined, '⑤ 体型被写到了顶层');

// 职业：写入 background.occupation
await wizard.randomizeField('occupation');
assert.equal(wizard.profile.background.occupation, '摄影师',
             '⑤ 职业随机未写入 background.occupation');

// 性格：单维随机只改该维
const bigFiveBefore = {...wizard.profile.personality.big_five};
await wizard.randomizeField('big_five', 'openness');
assert.equal(wizard.profile.personality.big_five.openness, 0.9,
             '⑤ 性格单维未更新');
for (const key of ['conscientiousness', 'extraversion', 'agreeableness',
                   'neuroticism']) {
  assert.equal(wizard.profile.personality.big_five[key],
               bigFiveBefore[key],
               `⑤ 点一维却把 ${key} 也改了`);
}

// ── 用例 ⑥：创建后刷新列表 + 再次进入向导会重置 ──────────────
const wizard2 = characterCreatePage();
wizard2.$nextTick = (fn) => fn();
wizard2.templates = [{
  id: 'gentle_artist', name: '温柔艺术家', icon: '🌿',
  theme: {primary: '#C4A882', soft: '#E8DDD0', deep: '#8B7355'},
  defaults: {name: '', gender: 'female', age: 24,
             personality: {big_five: {openness: 0.8}, traits: ['温柔']}},
}];

// 模拟"创建过一次"的残留状态
wizard2.selectTemplate('gentle_artist');
assert.equal(wizard2.currentStep, 1, '⑥ 选模板后应进配置步');
wizard2.profile.name = '上一个角色';
wizard2.currentStep = 2;

// 再次进入：reset 必须清干净（否则还是刚才的角色）
wizard2.reset();
assert.equal(wizard2.currentStep, 0, '⑥ 重置后应回第 0 步');
assert.equal(wizard2.selectedTemplate, null, '⑥ 重置后不应保留模板选择');
assert.equal(wizard2.profile.name, '', '⑥ 重置后不应保留上个角色的名字');

// 走一遍创建：store 的角色列表必须被刷新（侧边栏才会显示）
wizard2.selectTemplate('gentle_artist');
wizard2.profile.name = '新角色';
await wizard2.create();
const sidebarCharacters = Alpine.store('app').characters;
assert.deepEqual(sidebarCharacters, [{id: 'c1', name: '侧边栏角色'}],
                 '⑥ 创建后 store.characters 未刷新（侧边栏不更新的根因）');

// ── 用例 ⑦：重新生成 / 左滑删除 / 长按复制 ──────────────────
let copiedText = null;
Object.defineProperty(globalThis, 'navigator', {
  value: {clipboard: {writeText: async (text) => { copiedText = text; }}},
  configurable: true,
});

const chat2 = chatPage();
chat2.$nextTick = (fn) => fn();
chat2.$refs = {messages: {scrollHeight: 100, clientHeight: 100,
                          scrollTop: 0}};
await chat2.enter();                     // 加载 test-char + 空历史
chat2.input = '你好';
await chat2.send();                      // 发一条 → 用户 + 助手两条
assert.equal(chat2.messages.length, 2, '⑦ 前置：应有两条消息');
const beforeContent = chat2.messages[1].content;

// 重新生成：去掉助手回复并重发（用户气泡不能重复）
await chat2.regenerate();
const userBubbles = chat2.messages.filter((m) => m.role === 'user');
assert.equal(userBubbles.length, 1, '⑦ 重新生成后用户气泡不应重复');
assert.equal(chat2.messages.at(-1).content, beforeContent,
             '⑦ 重新生成后应有新的助手回复');

// 长按复制：500ms 后应复制该条内容
const target = chat2.messages[0];
chat2.onMessageTouchStart(target, {clientX: 10, clientY: 10});
await new Promise((resolve) => setTimeout(resolve, 650));
assert.equal(copiedText, target.content, '⑦ 长按应复制该条消息内容');

// 左滑：超过阈值露出删除（swipedMessageId），回滑取消
chat2.onMessageTouchStart(target, {clientX: 0, clientY: 0});
chat2.onMessageTouchMove({touches: [{clientX: -100, clientY: 0}]});
assert.equal(chat2.swipedMessageId, target.id, '⑦ 左滑应露出删除按钮');
chat2.onMessageTouchMove({touches: [{clientX: 100, clientY: 0}]});
assert.equal(chat2.swipedMessageId, null, '⑦ 右回应取消删除');
chat2.onMessageTouchEnd();

// 删除：从当前视图移除
const countBefore = chat2.messages.length;
chat2.deleteMessage(target);
assert.equal(chat2.messages.length, countBefore - 1,
             '⑦ 删除应从视图移除');

console.log('① send() 未就绪不崩（无气泡/不清空）        OK');
console.log('② 就绪后 SSE 发送 characterId 正确           OK');
console.log('③ 重复 connect 单连接 + 认证帧发对 socket     OK');
console.log('④ WS 处理器只绑一次（load 两次）              OK');
console.log('⑤ 向导 🎲：名字/体型/职业/性格单维 路径正确  OK');
console.log('⑥ 创建后刷新列表 + 再进入向导重置            OK');
console.log('⑦ 重新生成/左滑删除/长按复制                  OK');
console.log('组件级冒烟全部通过');

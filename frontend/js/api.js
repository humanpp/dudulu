// API 封装（文档 3 第 9.2 节，v1.4：注释直接语义化 + X-Timezone 兜底注明）。
// 统一处理 Token 附加、X-Timezone 上报与 401 跳转。

const BASE_URL = '/api';

async function request(path, options = {}) {
  const token = Alpine.store('app').token;
  const headers = {
    'Content-Type': 'application/json',
    // 用户时区随请求上报，后端同步进 users.timezone 供 quiet_hours 判定
    // （文档 2 第 3.11 节）；兜底与后端缺省值同为 Asia/Shanghai
    'X-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      || 'Asia/Shanghai',
    ...options.headers,
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    Alpine.store('app').clearToken();
    window.location.hash = '#/login';
    throw new Error('认证过期');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || '请求失败');
  }

  return response.json();
}

export const api = {
  // 认证
  register: (data) => request('/auth/register',
    {method: 'POST', body: JSON.stringify(data)}),
  login: (data) => request('/auth/login',
    {method: 'POST', body: JSON.stringify(data)}),

  // 会话
  createSession: (characterId) => request('/session/create',
    {method: 'POST', body: JSON.stringify({character_id: characterId})}),

  // 角色（archived=true 只看自己已归档的，用于恢复入口）
  listCharacters: (archived = false) =>
    request(`/character/list${archived ? '?archived=true' : ''}`),
  getCharacter: (id) => request(`/character/${id}`),
  createFromTemplate: (data) => request('/character/create/template',
    {method: 'POST', body: JSON.stringify(data)}),
  createRandom: (data) => request('/character/create/random',
    {method: 'POST', body: JSON.stringify(data)}),
  createManual: (data) => request('/character/create/manual',
    {method: 'POST', body: JSON.stringify(data)}),
  updateCharacter: (id, updates) => request(`/character/${id}`,
    {method: 'PUT', body: JSON.stringify({updates})}),

  // 预览随机（不落库）
  previewRandomField: (data) => request('/character/preview/random-field',
    {method: 'POST', body: JSON.stringify(data)}),
  previewRandomAll: (data) => request('/character/preview/random-all',
    {method: 'POST', body: JSON.stringify(data)}),
  previewValidate: (data) => request('/character/preview/validate',
    {method: 'POST', body: JSON.stringify(data)}),

  // 模板
  listTemplates: () => request('/template/list'),

  // 状态
  getState: (characterId) => request(`/state?character_id=${characterId}`),

  // 记忆
  searchMemory: (params) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/memory/search?${qs}`);
  },

  // 会话（v1.1 补全）
  endSession: (sessionId) => request(`/session/${sessionId}/end`,
    {method: 'POST'}),
  getActiveSession: (characterId) =>
    request(`/session/active?character_id=${characterId}`),

  // 最近消息（聊天页进入时加载历史）
  getRecentMessages: (characterId, limit = 20) =>
    request(`/messages/recent?character_id=${characterId}&limit=${limit}`),

  // 认证
  updatePassword: (data) => request('/auth/password',
    {method: 'PUT', body: JSON.stringify(data)}),
  // logout 不需要端点：clearToken() + wsManager.disconnect() + 跳 #/login

  // 角色（角色编辑页与设置页）
  switchCharacter: (id) => request(`/character/${id}/switch`,
    {method: 'POST'}),
  archiveCharacter: (id) => request(`/character/${id}`,
    {method: 'DELETE'}),
  restoreCharacter: (id) => request(`/character/${id}/restore`,
    {method: 'POST'}),
  exportCharacter: (id) => request(`/character/${id}/export`),
  importCharacter: (profile) => request('/character/import',
    {method: 'POST', body: JSON.stringify({profile})}),
  getCharacterVersions: (id) => request(`/character/${id}/versions`),
  rollbackCharacter: (id, versionId) =>
    request(`/character/${id}/rollback/${versionId}`, {method: 'POST'}),

  // 配置（v1.1 补全）
  getLLMConfig: () => request('/config/llm'),
  // updateLLMConfig 的 api_key 语义：请求体不出现该字段 = 保持原值；
  // api_key: '' = 清除。用户没动 Key 输入框时不要带这个字段。
  updateLLMConfig: (data) => request('/config/llm',
    {method: 'PUT', body: JSON.stringify(data)}),
  getProactiveConfig: () => request('/config/proactive'),
  updateProactiveConfig: (data) => request('/config/proactive',
    {method: 'PUT', body: JSON.stringify(data)}),
};

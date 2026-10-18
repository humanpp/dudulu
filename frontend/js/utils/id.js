// 生成临时 id（文档 3 第 4 章 id.js，v1.1 新增）。
// 用于消息列表 :key（后端 rid 落库前的前端占位）。

export function generateId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'id-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

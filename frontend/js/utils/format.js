// 时间/数字格式化（文档 3 第 4 章 format.js）。

/** 消息时间戳 → HH:MM（气泡内右下角）。 */
export function formatTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return `${String(date.getHours()).padStart(2, '0')}:${
    String(date.getMinutes()).padStart(2, '0')}`;
}

/** 相对时间：x 分钟前 / x 小时前 / 昨天 / x 天前 / YYYY-MM-DD（记忆卡片用）。 */
export function formatRelative(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const now = Date.now();
  const diffMinutes = Math.floor((now - date.getTime()) / 60000);
  if (diffMinutes < 1) return '刚刚';
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} 小时前`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return '昨天';
  if (diffDays < 30) return `${diffDays} 天前`;
  const diffWeeks = Math.floor(diffDays / 7);
  if (diffWeeks < 5) return `${diffWeeks} 周前`;
  return formatDate(timestamp);
}

/** 日期 → YYYY-MM-DD。 */
export function formatDate(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${
    String(date.getDate()).padStart(2, '0')}`;
}

/** 版本历史时间：YYYY-MM-DD HH:MM（文档 3 第 6.5 节）。 */
export function formatDateTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return `${formatDate(timestamp)} ${formatTime(timestamp)}`;
}

/** 消息气泡时间戳：YYYY-MM-DD HH:mm:ss（用户要求带秒，2026-10-18）。 */
export function formatTimestamp(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${formatDateTime(timestamp)}:${seconds}`;
}

/** 认识天数（关系条 meta 行）：从角色状态 last_updated 起算的近似值。 */
export function formatDaysSince(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const days = Math.floor((Date.now() - date.getTime()) / 86400000);
  return days <= 0 ? '今天' : `认识 ${days} 天`;
}

/** 数值 → 两位小数字符串（状态面板展示）。 */
export function formatValue(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '0.00';
  return Number(value).toFixed(2);
}

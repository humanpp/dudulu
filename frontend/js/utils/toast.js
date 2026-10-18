// 轻提示（文档 3 第 4 章 toast.js，v1.1 新增）。
// 错误与操作反馈；容器常驻 DOM，toast 自动消失。

const DEFAULT_DURATION = 3000;

function ensureContainer() {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
  }
  return container;
}

/** 显示一条 toast（type: '' | 'error' | 'success'）。 */
export function showToast(message, type = '', duration = DEFAULT_DURATION) {
  const container = ensureContainer();
  const element = document.createElement('div');
  element.className = `toast${type ? ` toast--${type}` : ''}`;
  element.textContent = message;
  container.appendChild(element);
  setTimeout(() => {
    element.classList.add('toast--leaving');
    setTimeout(() => element.remove(), 250);
  }, duration);
}

/** 错误 toast（文档 3 第 7.6 节：API 错误反馈）。 */
export function showError(message) {
  showToast(message, 'error', 4000);
}

/** 成功 toast。 */
export function showSuccess(message) {
  showToast(message, 'success');
}

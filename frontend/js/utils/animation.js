// 动效工具（文档 3 第 4 章 animation.js：数值滚动、呼吸动画）。

/**
 * 数值滚动动画：从 from 到 to，duration ms，逐帧写回 apply 回调。
 * 用于状态面板的数字变化（文档 3 第 7.3 节第 2 条）。
 */
export function animateNumber(from, to, duration, apply) {
  if (typeof requestAnimationFrame === 'undefined') {
    apply(to);
    return;
  }
  const start = performance.now();
  const delta = to - from;
  function frame(now) {
    const progress = Math.min((now - start) / duration, 1);
    // ease-out
    const eased = 1 - Math.pow(1 - progress, 3);
    apply(from + delta * eased);
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/**
 * 一次性呼吸脉冲：给元素加 class，600ms 后移除
 * （文档 3 第 9.6 节：连续显著变化可重复触发）。
 */
export function breatheOnce(element, className = 'state-panel--breathing',
                            duration = 600) {
  if (!element) return;
  element.classList.remove(className);
  // 强制重排，保证连续触发时动画重新开始
  void element.offsetWidth;
  element.classList.add(className);
  setTimeout(() => element.classList.remove(className), duration);
}

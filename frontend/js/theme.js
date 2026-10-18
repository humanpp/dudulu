// 角色主题色切换（文档 3 第 3.2 节）。

import {hexToRgba} from './utils/color.js';

/** 应用角色主题色（写 CSS 自定义属性，@property 保证平滑过渡）。 */
export function applyCharacterTheme(theme) {
  const root = document.documentElement;
  if (!theme) {
    root.style.removeProperty('--accent-primary');
    root.style.removeProperty('--accent-soft');
    root.style.removeProperty('--accent-deep');
    root.style.removeProperty('--accent-ghost');
    return;
  }
  root.style.setProperty('--accent-primary', theme.primary);
  root.style.setProperty('--accent-soft', theme.soft);
  root.style.setProperty('--accent-deep', theme.deep);
  root.style.setProperty('--accent-ghost', hexToRgba(theme.primary, 0.08));
}

/** 角色头像 emoji（模板 icon；无则回退角色名首字）。 */
export function characterAvatar(character) {
  return character?.icon || character?.avatar || character?.name?.charAt(0)
    || '🌿';
}

// 颜色工具（文档 3 第 3.2 / 3.3 节：hexToRgba + ensureContrast）。

/** #RRGGBB → rgba(r, g, b, alpha)。 */
export function hexToRgba(hex, alpha) {
  const normalized = String(hex || '').replace('#', '');
  const full = normalized.length === 3
    ? normalized.split('').map((c) => c + c).join('')
    : normalized;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 相对亮度（WCAG）。 */
function luminance(hex) {
  const normalized = String(hex || '').replace('#', '');
  const channel = (offset) => {
    const value = parseInt(normalized.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928
      ? value / 12.92
      : Math.pow((value + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
}

/** 两色对比度（WCAG）。 */
export function contrastRatio(hexA, hexB) {
  const first = luminance(hexA);
  const second = luminance(hexB);
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}

/** 加深/提亮 hex（amount 0~1，正值加深）。 */
function shade(hex, amount) {
  const normalized = String(hex || '').replace('#', '');
  const channel = (offset) => {
    const value = parseInt(normalized.slice(offset, offset + 2), 16) || 0;
    const shifted = amount >= 0
      ? Math.round(value * (1 - amount))
      : Math.round(value + (255 - value) * -amount);
    return Math.max(0, Math.min(255, shifted));
  };
  const toHex = (value) => String(value).padStart(2, '0');
  return `#${toHex(channel(0))}${toHex(channel(2))}${toHex(channel(4))}`;
}

/**
 * 保证背景色与文字色对比度达到 WCAG AA（4.5:1）；
 * 不达标则逐步加深背景（文档 3 第 3.3 节：改背景不改文字颜色）。
 * 返回 {background, text}。
 */
export function ensureContrast(backgroundHex, textHex, minRatio = 4.5) {
  let background = backgroundHex;
  if (contrastRatio(background, textHex) >= minRatio) {
    return {background, text: textHex};
  }
  // 文字深色时加深背景；文字浅色时也加深背景（白色文字配深背景）
  for (let step = 1; step <= 10; step += 1) {
    background = shade(backgroundHex, step * 0.06);
    if (contrastRatio(background, textHex) >= minRatio) break;
  }
  return {background, text: textHex};
}

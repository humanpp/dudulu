// 真实渲染验证：jsdom + vendor Alpine 实际渲染「性格」区块。
//
// 为什么值得单独一个脚本：Alpine 的绑定语义踩过三次坑
//（裸模块名 / 启动时序 / x-for 对象遍历的解构顺序），纯静态断言看不出来。
//
// 用法（可选，需要 jsdom）：
//   cd frontend && npm install jsdom && node check-render.mjs
// 没装 jsdom 时打印跳过提示并以 0 退出（不算失败）。

import {readFileSync} from 'node:fs';

let JSDOM;
try {
  ({JSDOM} = await import('jsdom'));
} catch {
  console.log('跳过：未安装 jsdom（npm install jsdom 后可跑真实渲染验证）');
  process.exit(0);
}

const indexHtml = readFileSync(
  new URL('./index.html', import.meta.url), 'utf-8');
const alpineSource = readFileSync(
  new URL('./assets/vendor/alpine.min.js', import.meta.url), 'utf-8');

// 从 index.html 原样抽出「性格」区块（保留真实绑定）
const titleIndex = indexHtml.indexOf(
  'wizard__config-group-title">性格</div>');
const blockStart = indexHtml.lastIndexOf('<div class="wizard__config-group">',
                                        titleIndex);
const blockEnd = indexHtml.indexOf(
  'wizard__config-group-title">背景与性格', titleIndex);
if (titleIndex === -1 || blockStart === -1 || blockEnd === -1) {
  console.error('✗ 没能从 index.html 抽到「性格」区块');
  process.exit(1);
}
const block = indexHtml.slice(blockStart, blockEnd);

const LABELS = {openness: '开放性', conscientiousness: '尽责性',
                extraversion: '外向性', agreeableness: '宜人性',
                neuroticism: '神经质'};

async function render(bigFive) {
  // x-data 里要带函数（bigFiveLabel），JSON.stringify 会丢函数，故手写字符串
  const dataAttr = `{"profile": ${
    JSON.stringify({personality: {big_five: bigFive}})}, ` +
    `"bigFiveLabel": (key) => (${JSON.stringify(LABELS)})[key] || key}`;
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
<div x-data='${dataAttr}'>
${block}
</div>
</body></html>`, {runScripts: 'outside-only', pretendToBeVisual: true});
  dom.window.eval(alpineSource);
  await new Promise((resolve) => setTimeout(resolve, 300));
  const rows = [...dom.window.document.querySelectorAll('.bigfive-row')];
  return {
    count: rows.length,
    labels: rows.map((row) =>
      row.querySelector('.bigfive-row__label')?.textContent),
    values: rows.map((row) =>
      Number(row.querySelector('.bigfive-row__value')?.textContent)),
    widths: rows.map((row) =>
      row.querySelector('.bigfive-row__fill')?.style.width),
    diceTitles: rows.map((row) =>
      row.querySelector('button')?.getAttribute('title')),
    // 维度键（用于单维随机）应能从 title 反推
    keys: rows.map((row) => {
      const title = row.querySelector('button')?.getAttribute('title') || '';
      return Object.keys(LABELS).find((key) => title === `随机${LABELS[key]}`);
    }),
  };
}

let failed = false;

// 场景 1：模板档案（gentle_artist 的真实取值）
const templateResult = await render({openness: 0.82, conscientiousness: 0.55,
  extraversion: 0.40, agreeableness: 0.75, neuroticism: 0.45});
const expectedOrder = ['openness', 'conscientiousness', 'extraversion',
  'agreeableness', 'neuroticism'];
const templateOk = templateResult.count === 5
  && templateResult.labels.join(',') === expectedOrder.map(
    (key) => LABELS[key]).join(',')
  && templateResult.values[0] === 0.82
  && templateResult.widths.every((width) => width.endsWith('%')
    && !width.includes('NaN'))
  && templateResult.keys.join(',') === expectedOrder.join(',');
console.log('模板档案 →', templateOk ? 'OK' : 'FAIL',
  '| 行数', templateResult.count,
  '| 标签', templateResult.labels.join(','),
  '| 宽度', templateResult.widths.join(','));
if (!templateOk) failed = true;

// 场景 2：空白创建（五维中性起点，不能是空对象）
const blankResult = await render({openness: 0.5, conscientiousness: 0.5,
  extraversion: 0.5, agreeableness: 0.5, neuroticism: 0.5});
const blankOk = blankResult.count === 5
  && blankResult.labels.join(',') === expectedOrder.map(
    (key) => LABELS[key]).join(',');
console.log('空白档案 →', blankOk ? 'OK' : 'FAIL',
  '| 行数', blankResult.count,
  blankResult.count === 5 ? '' : '（空对象会一行都不渲染！）');
if (!blankOk) failed = true;

console.log(failed ? '✗ 渲染验证失败' : '✓ 性格区块真实渲染正确');
process.exit(failed ? 1 : 0);

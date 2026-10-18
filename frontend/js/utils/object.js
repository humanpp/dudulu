// 对象路径读写（文档 3 第 4 章 object.js，v1.1 新增）。

/** 按点分路径写值（路径不存在则逐级建对象）。 */
export function setNestedValue(target, path, value) {
  if (!target || typeof target !== 'object') return target;
  const keys = String(path).split('.');
  let cursor = target;
  for (let index = 0; index < keys.length - 1; index += 1) {
    const key = keys[index];
    if (typeof cursor[key] !== 'object' || cursor[key] === null) {
      cursor[key] = {};
    }
    cursor = cursor[key];
  }
  cursor[keys[keys.length - 1]] = value;
  return target;
}

/** 按点分路径读值（不存在返回 undefined）。 */
export function getNestedValue(source, path) {
  if (!source || typeof source !== 'object') return undefined;
  return String(path).split('.').reduce(
    (cursor, key) => (cursor === null || cursor === undefined
      ? undefined : cursor[key]), source);
}

/** 深拷贝（JSON 安全数据）。 */
export function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

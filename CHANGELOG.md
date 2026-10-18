# 变更日志

本项目按 Phase 推进，每个 Phase 交付后经评审裁决再进入下一阶段。
版本号规则：`0.x` = 一期功能建设期，`1.0` = 一期完成（Phase 10 交付）。

## [0.10] - 2026-10-18 · 收尾打磨

- 修复：角色列表单一数据源（创建/归档/恢复/导入后侧边栏与选择页同步刷新）
- 修复：创建向导再次进入时重置状态（不再停留在上一个角色的预览步）
- 修复：消息气泡显示完整时间戳 `YYYY-MM-DD HH:mm:ss`
- 修复：流式输出时用户上翻历史不再被强行拽回底部（离底部 80px 内才自动滚）
- 修复：WS 重复连接往 CONNECTING 中的连接发认证帧导致的 `DOMException`
- 修复：`send()` 在页面未就绪时抛 `TypeError`（消息卡死在"发送中"）
- 修复：WS 事件处理器重复绑定（来回切角色后一条消息更新/弹 N 次）
- 修复：`preview/validate` 对部分档案返回 500（缺 `id` 即 `KeyError`）
- 修复：`/template/list` 不返回 `defaults`/`theme`（向导选完模板档案是空的）
- 修复：体型/职业/性格随机按钮写入错误层级（`body_type` 应为 `appearance.body_type` 等）
- 修复：性格随机"点一个五个都变"（改为按维度单独随机）
- 修复：体型随机恒返回模板固定值（单字段随机忽略模板默认）
- 修复：前端下拉 value 用中文、后端是英文枚举导致选不中（体型/性别）
- 修复：空白创建的五维为空对象，`x-for` 遍历空对象导致「性格」区块消失
- 修复：登录后 `store.user` 未设置（设置页用户名显示 `—`）；退出登录不清残留数据
- 修复：未知路由主区域空白；移动端侧边栏补遮罩；记忆浏览器返回按钮、YAML 导入误导
- 新增：设置页 API 模式的 `base_url` 可自行修改（换网关/服务商不用改 `.env`）
- 新增：聊天四项打磨——打字指示器、发送旋转圆点、空对话欢迎语、稀有角色 ✨
- 新增：重新生成、左滑删除（视图级）、长按复制、离线横幅
- 新增：`GET /character/list?archived=true` 已归档角色入口与恢复 UI
- 新增：`backend/run.py`（Windows 友好的单进程启动，规避 `--reload` 退出卡死）
- 新增：`frontend/check-modules.mjs` / `check-components.mjs` / `check-render.mjs`
  （Node 侧模块图、组件行为、jsdom+Alpine 真实渲染三级冒烟）
- 新增：静态资源 `Cache-Control: no-cache` + CSS 版本号，根治"改了不生效"
- 文档：README 重写（conda 环境、配置表、API 示例、测试、FAQ、已知限制）

## [0.9] - 2026-10-17 · 前端全部页面

- 前端：设计变量 + 布局系统 + 13 个组件 + 8 个页面 + Alpine 组件全套
- 前端：静态托管挂在 `/`，hash 路由，`Cache-Control` 前的启发式缓存问题自此暴露
- 后端：补齐 `/api/config/llm` 与 `/api/config/proactive`（文档 2 §6.10）
- E2E：`scripts/e2e_smoke.py` 15 项真实链路冒烟（真实 uvicorn + 真实 LLM）

## [0.8b] - 2026-10-16 · 主动行为引擎 + WebSocket

- 主动引擎：五道硬性拦截、六种触发条件 + P0-P4 优先级、eagerness 参数
- WS：跨线程推送（捕获连接所在事件循环）、`state_update` / `proactive_message`
- 管道第 11.5 步接入真实 `ws_manager`；顺序：存储 → 推送 → 沉淀 → done

## [0.8a] - 2026-10-15 · 时区对齐

- `db/timezone.py` 单一来源：日/周边界按用户本地时区（`X-Timezone` 落库）
- 沉淀（会话/日/周）全部改按用户本地日界计算

## [0.7] - 2026-10-14 · 记忆沉淀调度

- `memory/sedimenter.py`：会话/日/周三分支，峰值 + 尾声加权
- 慢心境基线更新：`day_end_expression` 成为次日快情绪衰减目标
- `scheduler/jobs.py`：APScheduler 异步任务 + `SCHEDULER_ENABLED` 开关
- 沉淀幂等：同一 `(character_id, user_id)` 同一天只允许一条 `daily_snapshot`

## [0.6] - 2026-10-13 · 角色管理层

- `core/character/` 全套：档案/管理器/模板加载/随机生成/校验/覆盖/主题
- 8 个内置模板 + 3 个姓名池 + 5 个随机池
- 角色 13 端点 + 预览随机 3 端点 + 模板 5 端点
- 裁决⑤的三处档案来源切换到真实 `CharacterManager`

## [0.5] - 2026-10-12 · 记忆系统

- YantrikDB 接入：混合检索、`(character_id, user_id)` 双键隔离
- 记忆衰减堆：`importance ≥ 0.7`、`decay < 0.5`、`remaining < 72h`、`top_k 50`
- 召回强化上限：`half_life` 最大 = 初始值 × 3
- `recall()` 隐藏副作用（强化 half_life）以 `skip_reinforce=True` 规避

## [0.4] - 2026-10-11 · 用户与角色基础

- JWT 认证、用户隔离、会话/消息/状态端点
- `PlaceholderCharacterManager` 引入（Phase 6 替换）

## [0.3] - 2026-10-10 · 编排管道

- 12 步管道：分类 → 情感更新 → 记忆检索 → Prompt 装配 → LLM → 存储 → 沉淀
- SSE 流式（`token`/`done`/`error` 三事件，`\n\n` 分隔）

## [0.2] - 2026-10-09 · LLM 统一接口

- `APIClient` / `LocalClient` 双模式，`embed` 接口保留并显式 `NotImplementedError`
- 工厂 + Settings 驱动的双模式切换

## [0.1] - 2026-10-08 · 情感引擎

- 三层数值（表达/内部/情感）、事件更新、时间衰减、软钳制
- Big Five 对内部层初始状态的调节

---

### 已知限制（一期范围外）

- 跨进程 WS 推送未实现（生产独立调度进程下降级为落库 + 拉取）
- 真正本地部署（ollama/vLLM）连通性未实测
- 期待感检测使用关键词（等分类器加 `anticipation_signal` 字段）
- 系统预置角色未种子化
- LoRA 微调不在范围
- 左滑删除为视图级移除（后端暂无删除消息端点，刷新后恢复）

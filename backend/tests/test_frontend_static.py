"""前端静态托管与资源完整性冒烟（Phase 9）。

覆盖：main.py 的 StaticFiles 挂载、index.html 引用的全部 CSS/JS 可访问、
JS 模块 import 路径全部可解析、SPA 入口行为。
不启动调度器（monkeypatch get_settings 关掉），不碰真实数据。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def strip_comments(text: str) -> str:
    """剥掉 JS 注释再扫描——警示性注释里会提到被禁的写法
    （如 app.js 注释说明为何不能 `import from 'alpinejs'`），
    不能误伤。"""
    import re as regex

    text = regex.sub(r"/\*.*?\*/", "", text, flags=regex.DOTALL)
    text = regex.sub(r"//[^\n]*", "", text)
    return text


@pytest.fixture
def client(monkeypatch):
    """main.app + 关闭调度器（避免冒烟启动真实 APScheduler）。"""
    import scheduler.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "get_settings",
                        lambda: Settings(_env_file=None,
                                         SCHEDULER_ENABLED=False))
    from main import app

    with TestClient(app) as test_client:
        yield test_client


class TestStaticServing:
    def test_index_served_at_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "dudulu" in response.text
        assert 'src="js/app.js"' in response.text

    def test_index_references_all_resolve(self, client):
        """index.html 引用的每个 css/js 资源都返回 200。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        references = re.findall(r'(?:href|src)="((?:css|js)/[^"]+)"', index)
        # 3 基础 + 12 组件 + 8 页面 CSS + app.js 入口 = 24
        assert len(references) >= 24, f"引用过少：{len(references)}"
        missing = []
        for reference in references:
            response = client.get(f"/{reference}")
            if response.status_code != 200:
                missing.append((reference, response.status_code))
        assert not missing, f"资源缺失：{missing}"

    def test_missing_asset_404(self, client):
        """不存在的资源返回 404（不被 SPA 回退掩盖）。"""
        assert client.get("/css/nope.css").status_code == 404
        assert client.get("/js/nope.js").status_code == 404

    def test_static_no_cache_header(self, client):
        """防回归：静态文件必须带 Cache-Control: no-cache。

        Starlette 默认不发该头，浏览器走启发式缓存——改了 CSS/JS
        用户强刷都可能拿旧文件（2026-10-17 实测：滚动条修复不生效，
        日志里一条 CSS 请求都没有）。
        """
        response = client.get("/css/layout.css")
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-cache"
        # 修复内容确实在下发的文件里
        assert ".main-area > .character-create" in response.text
        assert "overflow-y: auto" in response.text

    def test_index_html_entry_explicit(self, client):
        """/index.html 显式路径也可达（hash 路由下浏览器只请求 /）。"""
        response = client.get("/index.html")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_hash_route_never_hits_server(self):
        """hash 路由（#/chat/x）的路径段不会发到服务端——
        浏览器只请求 /，路由由前端 router.js（经 app.js 模块图加载）解析。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert 'src="js/app.js"' in index  # 唯一脚本入口
        app_js = (FRONTEND_DIR / "js/app.js").read_text(encoding="utf-8")
        assert "from './router.js'" in app_js  # 路由经模块图加载
        router = (FRONTEND_DIR / "js/router.js").read_text(encoding="utf-8")
        assert "window.location.hash" in router  # hash 路由，非 history


class TestJsModuleGraph:
    def test_all_imports_resolve(self):
        """每个 JS 文件的相对 import 都指向真实文件。"""
        js_files = sorted(FRONTEND_DIR.glob("js/**/*.js"))
        assert len(js_files) >= 15
        unresolved = []
        for js_file in js_files:
            text = js_file.read_text(encoding="utf-8")
            for specifier in re.findall(r"from '(\.[^']+)'", text):
                target = (js_file.parent / specifier).resolve()
                if not target.is_file():
                    unresolved.append((js_file.name, specifier))
        assert not unresolved, f"import 无法解析：{unresolved}"

    def test_alpine_data_names_registered(self):
        """app.js 注册的 Alpine.data 与 index.html 的 x-data 名称一致。"""
        app_js = (FRONTEND_DIR / "js/app.js").read_text(encoding="utf-8")
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        registered = set(re.findall(r"[Aa]lpine\.data\('([^']+)'",
                                    strip_comments(app_js)))
        used = set(re.findall(r'x-data="(\w+)\(\)"', index))
        assert used, "index.html 里没有 x-data"
        missing = used - registered
        assert not missing, f"未注册的组件：{missing}（已注册：{registered}）"

    def test_css_files_exist_for_each_link(self):
        """目录结构与文档 3 第 4 章一致（tokens/base/layout + 组件 + 页面）。"""
        expected = [
            "css/tokens.css", "css/base.css", "css/layout.css",
            "css/components/chat-bubble.css",
            "css/components/proactive-label.css",
            "css/components/emotion-ring.css",
            "css/components/emotion-bar.css",
            "css/components/character-card.css",
            "css/components/wizard.css",
            "css/components/button.css",
            "css/components/input.css",
            "css/components/modal.css",
            "css/components/toast.css",
            "css/components/skeleton.css",
            "css/pages/auth.css", "css/pages/chat.css",
            "css/pages/state-panel.css",
            "css/pages/character-select.css",
            "css/pages/character-create.css",
            "css/pages/character-edit.css",
            "css/pages/memory-viewer.css",
            "css/pages/settings.css",
        ]
        missing = [path for path in expected
                   if not (FRONTEND_DIR / path).is_file()]
        assert not missing, f"缺失样式文件：{missing}"

    def test_alpine_is_local_vendor(self):
        """Phase 10 裁决①：Alpine 走本地 vendor，无 CDN 外网依赖。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert "cdn.jsdelivr.net" not in index
        assert "unpkg.com" not in index
        vendor = FRONTEND_DIR / "assets/vendor/alpine.min.js"
        assert vendor.is_file(), "缺少 assets/vendor/alpine.min.js"
        assert vendor.stat().st_size > 10_000  # 43KB 左右的完整构建

    def test_no_bare_module_imports(self):
        """防回归：禁止 `import ... from 'alpinejs'` 这类裸模块名。

        浏览器没有 import map，解析不了裸模块名——整条模块图加载失败，
        页面全白（2026-10-17 实测事故）。Alpine 只能用 window.Alpine。
        （剥注释后扫描：警示注释里提到被禁写法不算违规。）
        """
        bare = []
        for js_file in FRONTEND_DIR.glob("js/**/*.js"):
            text = strip_comments(js_file.read_text(encoding="utf-8"))
            for specifier in re.findall(r"from '([^.'][^']*)'", text):
                bare.append((js_file.name, specifier))
        assert not bare, f"裸模块名导入（浏览器无法解析）：{bare}"

    def test_app_js_uses_window_alpine(self):
        """app.js 从 window.Alpine 取实例（vendor IIFE 暴露的全局）。"""
        app_js = strip_comments(
            (FRONTEND_DIR / "js/app.js").read_text(encoding="utf-8"))
        assert "window.Alpine" in app_js
        assert "from 'alpinejs'" not in app_js

    def test_app_js_loads_before_alpine(self):
        """防回归：app.js 必须排在 vendor Alpine 之前。

        vendor 构建结尾是 `window.Alpine=...; queueMicrotask(start)`
        ——微任务里立即启动，不等 DOMContentLoaded。顺序反了，
        Alpine 遍历 DOM 时组件还没注册，全线 "xxx is not defined"。
        """
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        app_pos = index.find('src="js/app.js"')
        alpine_pos = index.find('src="assets/vendor/alpine.min.js"')
        assert app_pos != -1 and alpine_pos != -1, "脚本标签缺失"
        assert app_pos < alpine_pos, (
            "app.js 必须出现在 vendor Alpine 之前（时序契约）")

    def test_app_js_registers_on_alpine_init(self):
        """防回归：组件注册走 alpine:init 事件（Alpine start() 时派发）。"""
        app_js = strip_comments(
            (FRONTEND_DIR / "js/app.js").read_text(encoding="utf-8"))
        assert "alpine:init" in app_js
        assert "addEventListener('alpine:init'" in app_js

    def test_dev_launcher_exists_without_reloader(self):
        """run.py：单进程启动（Windows Ctrl+C 卡死的规避方案）。"""
        run_py = FRONTEND_DIR.parent / "backend" / "run.py"
        assert run_py.is_file(), "缺少 backend/run.py"
        text = run_py.read_text(encoding="utf-8")
        assert "uvicorn.Server" in text
        assert 'reload=args.reload' in text      # 默认 False，需显式开启
        assert "should_exit" not in text.split("def main")[0]
        # 默认不启用 reload
        assert 'action="store_true"' in text

    def test_edit_page_has_gender_and_occupation(self):
        """角色编辑页能看到/改 性别与职业（2026-10-18 实测缺口）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        edit_block = index[index.index('class="character-edit"'):]
        edit_block = edit_block[:edit_block.index("<!-- 设置页")]
        assert 'x-model="form.gender"' in edit_block, "编辑页缺性别字段"
        assert 'x-model="form.occupation"' in edit_block, "编辑页缺职业字段"
        edit_js = (FRONTEND_DIR / "js/components/character-edit.js").read_text(
            encoding="utf-8")
        assert "gender: this.form.gender" in edit_js, "保存时未提交性别"
        assert "this.form.gender = profile.gender" in edit_js, "未回填性别"

    def test_select_page_has_archived_entry(self):
        """角色选择页有「已归档」入口与恢复按钮（2026-10-18 实测缺口）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert "toggleArchived()" in index
        assert "restore(character)" in index
        assert "已归档（" in index
        select_js = (FRONTEND_DIR / "js/components/character-select.js"
                     ).read_text(encoding="utf-8")
        assert "listCharacters(true)" in select_js
        assert "restoreCharacter" in select_js

    def test_sidebar_reads_store_character_list(self):
        """侧边栏必须读 store 的角色列表（创建/归档后统一刷新）。

        2026-10-18 实测：创建完侧边栏不显示——外壳自己缓存了一份
        characters，创建后没人刷新它。
        """
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        sidebar = index[index.index('class="sidebar"'):]
        sidebar = sidebar[:sidebar.index('</aside>')]
        assert "$store.app.characters" in sidebar, "侧边栏未读 store 列表"
        store_js = (FRONTEND_DIR / "js/store.js").read_text(encoding="utf-8")
        assert "async loadCharacters()" in store_js, "store 缺 loadCharacters"
        # 创建/归档/恢复后都要触发刷新
        for name in ("character-create.js", "character-edit.js"):
            text = (FRONTEND_DIR / "js/components" / name).read_text(
                encoding="utf-8")
            assert "loadCharacters()" in text, f"{name} 未在变更后刷新列表"

    def test_chat_interactions_wired(self):
        """文档 3 §7.2/§10.3：重新生成 / 左滑删除 / 长按复制 / 离线横幅。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert "regenerate()" in index and "canRegenerate" in index
        assert "deleteMessage(message)" in index
        assert "onMessageTouchStart" in index
        assert "chat-message__delete" in index
        assert "offline-banner" in index
        chat_js = (FRONTEND_DIR / "js/components/chat.js").read_text(
            encoding="utf-8")
        assert "LONG_PRESS_MS" in chat_js and "SWIPE_REVEAL_PX" in chat_js
        assert "clipboard.writeText" in chat_js
        shell_js = (FRONTEND_DIR / "js/components/shell.js").read_text(
            encoding="utf-8")
        assert "'offline'" in shell_js and "navigator.onLine" in shell_js

    def test_settings_base_url_editable(self):
        """API 模式下 base_url 可在设置页修改（换网关/服务商不动 .env）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        settings_block = index[index.index('x-data="settingsPage()'):]
        assert 'x-model="llm.baseUrl"' in settings_block
        js = (FRONTEND_DIR / "js/components/settings.js").read_text(
            encoding="utf-8")
        assert "base_url: this.llm.baseUrl.trim()" in js

    def test_chat_states_wired(self):
        """文档 3 §5.10/§7.1/§7.2/文档1§5.6：打字指示器/旋转圆点/
        欢迎语/稀有图标都必须接上（CSS 早就写好了，2026-10-18 补接）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        # ① 打字指示器：等待首 token
        assert "typing-indicator" in index
        assert "isSending && !streamingContent" in index
        # ② 发送中旋转圆点
        assert 'class="btn__spinner" x-show="isSending"' in index
        # ③ 空对话欢迎语
        assert "chat-empty" in index
        assert "!messages.length && !isStreaming && !isSending" in index
        # ④ 稀有角色图标（后端 is_rare 字段）
        assert 'class="character-card__rare"' in index
        assert 'x-show="character.is_rare"' in index

    def test_router_falls_back_for_unknown_routes(self):
        """未知路由不能留空白主区域（跳回角色页）。"""
        router = (FRONTEND_DIR / "js/router.js").read_text(encoding="utf-8")
        assert "KNOWN_ROUTES" in router and "isKnownRoute" in router

    def test_mobile_sidebar_has_mask(self):
        """移动端侧边栏滑出时带遮罩（文档 3 第 10.2 节）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert 'class="sidebar-mask"' in index
        layout = (FRONTEND_DIR / "css/layout.css").read_text(encoding="utf-8")
        assert ".sidebar-mask" in layout
        assert "sidebarOpen = false" in index  # 点遮罩关闭

    def test_chat_bubble_shows_full_timestamp(self):
        """消息气泡带完整时间戳（YYYY-MM-DD HH:mm:ss，2026-10-18 要求）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert "formatTimestamp(message.timestamp)" in index
        chat_js = (FRONTEND_DIR / "js/components/chat.js").read_text(
            encoding="utf-8")
        assert "formatTimestamp" in chat_js
        format_js = (FRONTEND_DIR / "js/utils/format.js").read_text(
            encoding="utf-8")
        assert "export function formatTimestamp" in format_js

    def test_blank_profile_seeds_big_five(self):
        """空白档案的五维必须有值（空对象 → x-for 一行不渲染）。

        2026-10-18 实测：点「空白创建」进 Step 2，「性格」组只剩一个
        空标题，看着像整个区块消失了。
        """
        js = (FRONTEND_DIR / "js/components/character-create.js").read_text(
            encoding="utf-8")
        blank_start = js.index("function blankProfile()")
        blank_end = js.index("visibility:", blank_start)
        blank = js[blank_start:blank_end]
        assert "big_five: {}" not in blank, "空白档案五维为空"
        for dim in ("openness", "conscientiousness", "extraversion",
                    "agreeableness", "neuroticism"):
            assert dim in blank, f"空白档案缺五维 {dim}"

    def test_wizard_x_for_object_binding_order(self):
        """Alpine 遍历对象的解构顺序是 (值, 键名)——写成 (key, value)
        会让标签空、宽度 NaN（2026-10-18 jsdom 实测）。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        title = index.index('wizard__config-group-title">性格</div>')
        block = index[title:index.index(
            'wizard__config-group-title">背景与性格', title)]
        assert 'x-for="(value, key) in' in block, (
            "big_five 的 x-for 必须是 (value, key)：Alpine 用 "
            "Object.entries，第一个绑定是值、第二个是键名")

    def test_wizard_select_values_match_backend_enums(self):
        """下拉框 value 必须用后端枚举（中文只做显示）。

        2026-10-18 实测：体型 option 用中文（纤细/匀称），后端值是
        slim/athletic/…，导致选了没反应、随机结果也显示不出来。
        """
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        wizard = index[index.index('class="character-create"'):]
        wizard = wizard[:wizard.index("<!-- 聊天页")]
        for value in ('value="slim"', 'value="athletic"',
                      'value="curvy"', 'value="plump"'):
            assert value in wizard, f"体型 option 缺后端枚举 {value}"
        for value in ('value="female"', 'value="male"',
                      'value="non_binary"'):
            assert value in wizard, f"性别 option 缺后端枚举 {value}"
        # 不允许再用中文当 value
        for chinese in ('value="纤细"', 'value="匀称"', 'value="中性"'):
            assert chinese not in wizard, f"仍有中文 value: {chinese}"

    def test_wizard_dice_buttons_wired(self):
        """四个 🎲 的绑定：名字/体型/职业/性格单维。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        wizard = index[index.index('class="character-create"'):]
        wizard = wizard[:wizard.index("<!-- 聊天页")]
        assert "randomizeField('name')" in wizard, "名字缺随机按钮"
        assert "randomizeField('body_type')" in wizard
        assert "randomizeField('occupation')" in wizard
        # 性格必须按维度随机（不能整体随机）
        assert "randomizeField('big_five', key)" in wizard, (
            "性格 🎲 未按维度随机（点一个会五个都变）")
        assert "randomizeField('big_five')\">" not in wizard

    def test_wizard_has_all_three_entry_points(self):
        """向导三入口（文档 1 第 13.3 节）：模板 / 随机创建 / 空白创建。"""
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        assert "createRandomNow()" in index, "缺少「随机创建」入口"
        assert "startBlank()" in index, "缺少「空白创建」入口"
        # 背景故事可自由编写
        assert "profile.background.current_state" in index
        assert "profile.background.occupation" in index
        assert "traitsText" in index, "缺少性格特质自由输入"

    def test_css_urls_are_cache_busted(self):
        """CSS 引用必须带 ?v=（绕开浏览器启发式缓存）。

        2026-10-17 实测：两次 CSS 修复都没生效，日志里一条 CSS
        请求都没有——浏览器连问都不问。
        """
        index = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        css_links = re.findall(r'href="(css/[^"]+)"', index)
        assert css_links, "没有 CSS 引用"
        missing = [link for link in css_links if "?v=" not in link]
        assert not missing, f"未加版本号的 CSS：{missing}"

    def test_pages_scroll_inside_main_area(self):
        """防回归：非聊天页面必须在 main-area 内滚动。

        2026-10-17 实测：页面用 min-height:100vh 又是 main-area
        （overflow:hidden）的直接子元素 → 超屏内容被裁、无滚动条，
        创建向导的「确认创建」滑不到。
        """
        layout = (FRONTEND_DIR / "css/layout.css").read_text(encoding="utf-8")
        for page in ("character-select", "character-create",
                     "character-edit", "memory-viewer", "settings"):
            assert (f".main-area > .{page}" in layout
                    and "overflow-y: auto" in layout), (
                f"{page} 缺少 main-area 内滚动规则")
        # 页面自身不应再用 min-height:100vh（登录页除外，它在布局外）
        for css_file in FRONTEND_DIR.glob("css/pages/*.css"):
            if css_file.name == "auth.css":
                continue
            assert "min-height: 100vh" not in css_file.read_text(
                encoding="utf-8"), f"{css_file.name} 仍有 min-height:100vh"

    def test_api_client_covers_documented_endpoints(self):
        """api.js 的封装覆盖文档 3 第 9.2 节清单的关键端点。"""
        api_js = (FRONTEND_DIR / "js/api.js").read_text(encoding="utf-8")
        for method in ("login", "register", "listCharacters", "getCharacter",
                       "createFromTemplate", "createRandom", "updateCharacter",
                       "previewRandomField", "previewRandomAll",
                       "previewValidate", "listTemplates", "getState",
                       "searchMemory", "getRecentMessages", "endSession",
                       "getActiveSession", "updatePassword", "switchCharacter",
                       "archiveCharacter", "restoreCharacter",
                       "exportCharacter", "importCharacter",
                       "getCharacterVersions", "rollbackCharacter",
                       "getLLMConfig", "updateLLMConfig",
                       "getProactiveConfig", "updateProactiveConfig"):
            assert method in api_js, f"api.js 缺少 {method}"

    def test_x_timezone_header_present(self):
        """契约：请求统一带 X-Timezone 头（文档 3 第 9.2 节）。"""
        api_js = (FRONTEND_DIR / "js/api.js").read_text(encoding="utf-8")
        sse_js = (FRONTEND_DIR / "js/sse.js").read_text(encoding="utf-8")
        assert api_js.count("X-Timezone") >= 1
        assert "X-Timezone" in sse_js
        assert "Asia/Shanghai" in api_js  # 兜底与后端同值

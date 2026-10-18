"""FastAPI 入口（文档 2 第 2 章）。

运行：uvicorn main:app --reload --port 8000（在 backend/ 目录下）。
启动时建表（幂等，只建缺失的表）；生产 schema 变更走 Alembic
（db/migrations）。调度器随进程启动受 Settings.SCHEDULER_ENABLED 控制
（文档 2 第 10.2 节：生产 Web 进程置 false，由 python -m scheduler.run
单独跑一份）。前端静态文件挂在 /（index.html + css/ + js/，Phase 9）。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes_auth import router as auth_router
from api.routes_character import router as character_router
from api.routes_chat import router as chat_router
from api.routes_config import router as config_router
from api.routes_memory import router as memory_router
from api.routes_message import router as message_router
from api.routes_session import router as session_router
from api.routes_state import router as state_router
from api.routes_template import router as template_router
from api.routes_ws import router as ws_router, ws_manager
from config.settings import get_settings
from db.sqlite import init_db
from db.sqlite import _engine as default_engine
from scheduler.jobs import start_scheduler, stop_scheduler

# 前端目录（backend/../frontend）；不存在时跳过挂载（纯后端部署）
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(default_engine)
    # 调度器随进程启动（SCHEDULER_ENABLED=false 时为空操作，文档 2 第 10.2 节）；
    # 传 WS 单例使主动消息在 Web 进程内可直接推送
    start_scheduler(ws_manager=ws_manager)
    yield
    stop_scheduler()
    # 释放连接池：避免重启时残留连接导致 "database is locked"，
    # 也让进程退出更干净（2026-10-18 排查退出卡死时补）
    default_engine.dispose()


app = FastAPI(title="dudulu", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(message_router, prefix="/api")
app.include_router(state_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(character_router, prefix="/api")
app.include_router(template_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(ws_router)  # WS /ws/events 不带 /api 前缀（文档 2 第 3.12 节）

# 前端静态托管（Phase 9）：挂在最后，不与 API 路由冲突；
# html=True 使 / 直接返回 index.html（hash 路由的入口）
if FRONTEND_DIR.is_dir():
    class NoCacheStaticFiles(StaticFiles):
        """开发期静态文件：每次都让浏览器重新验证。

        Starlette 默认不发 Cache-Control，浏览器走启发式缓存——
        改了 CSS/JS 后用户强刷都可能拿到旧文件（2026-10-17 实测：
        滚动条修复不生效，日志里一条 CSS 请求都没有）。
        no-cache = 每次问服务端（304 或新内容），不等启发式过期。
        生产环境建议改为带 hash 的长缓存 + CDN。
        """

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True),
              name="frontend")

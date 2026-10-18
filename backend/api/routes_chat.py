"""对话接口（文档 2 第 6.6 节）：非流式 POST /api/chat + 流式 POST /api/chat/stream。

两个端点均走 orchestrator.pipeline.Pipeline，user_id 从 JWT 强制注入
（文档 2 第 7.2 节用户隔离；Phase 3 的 DEV_USER_ID 占位已移除）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import sessionmaker

from auth.dependencies import get_current_user
from config.settings import Settings, get_settings
from core.character.manager import CharacterNotFoundError
from db.models import User
from db.sqlite import get_db, get_session_factory
from fastapi import HTTPException
from llm.base import LLMClient
from llm.factory import create_llm_client, llm_config_with_overrides
from orchestrator.pipeline import Pipeline, create_pipeline, memory_ids

logger = logging.getLogger(__name__)

router = APIRouter()

# WS 连接表单例（api.routes_ws）：管道 11.5 步的 state_update 推送目标
from api.routes_ws import ws_manager as _ws_manager  # noqa: E402


class ChatRequest(BaseModel):
    """POST /api/chat 请求体（文档 2 第 6.6 节）。"""

    character_id: str
    session_id: str
    message: str


class ChatStreamRequest(BaseModel):
    """POST /api/chat/stream 请求体（文档 2 第 6.6 节）。"""

    character_id: str
    session_id: str
    message: str


def get_llm_client(settings: Settings = Depends(get_settings),
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)
                   ) -> LLMClient:
    """按当前配置创建 LLM 客户端（Settings + config 表覆盖，覆盖优先）。

    输入：Settings、请求级 Session（读 config 表的 LLM 覆盖，Phase 9
        设置页 PUT /api/config/llm 即时生效；Phase 10 起为「全局默认 +
        当前用户覆盖」合并视图）、当前用户；
    输出：LLMClient；副作用：无（不发请求）。
    测试通过 dependency_overrides 替换为桩客户端。

    空 api_key 占位为 "EMPTY"（与 LocalClient 同惯例）：OpenAI SDK 拒绝
    空 key，占位保证客户端可构造，认证失败在调用时以 error 事件 surfaced。
    """
    from db.config_store import LLM_CONFIG_KEY, load_merged_config

    overrides = load_merged_config(db, LLM_CONFIG_KEY, current_user.id)
    config = llm_config_with_overrides(settings, overrides)
    if not config["llm"]["api"].get("api_key"):
        config["llm"]["api"]["api_key"] = "EMPTY"
    return create_llm_client(config)


def get_pipeline(
    llm_client: LLMClient = Depends(get_llm_client),
    settings: Settings = Depends(get_settings),
    db_factory: sessionmaker = Depends(get_session_factory),
    current_user: User = Depends(get_current_user),
) -> Pipeline:
    """装配编排管道（Phase 8 起全部协作者为真实实现，含 WS 推送单例）。"""
    return create_pipeline(llm_client,
                           emotion_config_path=settings.EMOTION_CONFIG_PATH,
                           db_session_factory=db_factory,
                           yantrik_path=settings.YANTRIK_PATH,
                           ws_manager=_ws_manager)


@router.post("/chat")
async def chat(body: ChatRequest,
               current_user: User = Depends(get_current_user),
               pipeline: Pipeline = Depends(get_pipeline)) -> dict:
    """非流式对话（文档 2 第 6.6 节）。

    输入：character_id / session_id / message；输出：{reply, state, memories_used}。
    """
    try:
        result = await pipeline.process(body.message, body.character_id,
                                        current_user.id, body.session_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"reply": result.reply, "state": result.state,
            "memories_used": memory_ids(result.memories)}


@router.post("/chat/stream")
async def chat_stream(body: ChatStreamRequest,
                      current_user: User = Depends(get_current_user),
                      pipeline: Pipeline = Depends(get_pipeline)
                      ) -> EventSourceResponse:
    """流式对话：逐 token 推送（文档 2 第 6.6 节 SSE 事件类型表）。

    事件序列：若干 token → done（含最终 state 与 memories_used）；
    中途失败以 error（含 partial）替代 done。
    """
    # sep="\n"：适配文档 3 第 9.3 节前端 SSE 客户端的 '\n\n' 切分
    # （裁决：后端适配前端契约；文档 2 第 6.6 节补注分隔符为 \n\n）。
    return EventSourceResponse(
        pipeline.process_stream(body.message, body.character_id,
                                current_user.id, body.session_id),
        sep="\n")

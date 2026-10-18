"""WebSocket 端点与管理器（文档 2 第 3.12 / 6.11 节）。

WS /ws/events：
- 连接后客户端先发 {"type": "auth", "token": ...}，服务端验证后回
  {"type": "auth_ok"}；失败回 {"type": "auth_error"} 并关闭
- 之后服务端推送两类消息：
  - state_update：对话轮次的状态变更（管道第 11.5 步）
  - proactive_message：主动行为消息（主动引擎）
- 推送携带 character_id，前端按当前角色过滤（一个用户可能开多个角色页）

进程模型注记：连接只存在于 Web 进程。主动引擎在 Web 进程内随调度器
运行时推送直达；在独立调度进程（python -m scheduler.run）里 ws_manager
为空，推送为空操作，消息仍落库（前端下次拉取可见）。生产形态的跨进程
推送（pub/sub 或把主动任务并入 Web 进程 + 领导者锁）待裁决，见交付报告。
"""

from __future__ import annotations

import asyncio
import logging

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth.jwt import verify_token

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSocketManager:
    """职责：管理所有活跃的 WebSocket 连接，按 user_id 分组（文档 2 第 3.12 节）。

    跨线程投递：连接注册时捕获其所在事件循环（端点协程的 loop）。
    push 可能从任意线程调用（请求线程、调度进程等），故：
    - 调用方 loop 就是连接 loop → 直接 create_task
    - 否则 → run_coroutine_threadsafe 投递到连接自己的 loop
    （实测踩坑：无脑用调用方 loop 的 create_task，跨线程时消息永远
    发不出去——连接属于 WS 门户的 loop，不属于请求线程。）
    """

    def __init__(self):
        """副作用：无（连接表懒建）。"""
        # user_id -> [(websocket, loop|None), ...]
        self.connections: dict[str, list[tuple[WebSocket, object]]] = {}

    def connect(self, user_id: str, ws: WebSocket) -> None:
        """登记一条已认证的连接（accept 由端点在认证前完成）。

        与文档 3.12 节示例的差异：示例在 connect 里 accept，但认证握手
        要求先 accept 才能收 auth 消息，故 accept 移到端点、connect 只登记。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None  # 同步上下文注册（测试）：loop 留空，push 时回退
        self.connections.setdefault(user_id, []).append((ws, loop))

    def disconnect(self, user_id: str, ws: WebSocket) -> None:
        """摘除连接；用户的连接列表为空时清理键。"""
        connections = self.connections.get(user_id)
        if not connections:
            return
        remaining = [(socket, loop) for socket, loop in connections
                     if socket is not ws]
        if remaining:
            self.connections[user_id] = remaining
        else:
            del self.connections[user_id]

    def push(self, user_id: str, message: dict) -> None:
        """推送消息给指定用户的所有连接（state_update / proactive_message）。"""
        connections = self.connections.get(user_id) or []
        if not connections:
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        for ws, loop in list(connections):
            coro = self._safe_send(ws, message)
            if loop is not None and loop.is_running() and loop is not \
                    current_loop:
                # 连接在别的线程的 loop 上：跨线程投递
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except RuntimeError as exc:  # loop 已关闭
                    logger.debug("WS 跨线程投递失败（loop 已关）: %s", exc)
            elif current_loop is not None:
                current_loop.create_task(coro)
            else:
                # 无任何可用 loop（同步上下文且连接未绑定 loop）：跳过
                logger.debug("无可用事件循环，跳过 WS 推送: %s", user_id)

    @staticmethod
    async def _safe_send(ws: WebSocket, message: dict) -> None:
        """单连接发送；失败（已断开）只记日志，不影响其余连接。"""
        try:
            await ws.send_json(message)
        except Exception as exc:  # 连接已关闭/发送失败
            logger.debug("WS 发送失败: %s", exc)

    def user_online(self, user_id: str) -> bool:
        """该用户是否有活跃连接（调试/测试用）。"""
        return bool(self.connections.get(user_id))


# 进程级单例：管道第 11.5 步与主动引擎共用同一张连接表
ws_manager = WebSocketManager()


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """WS /ws/events：认证握手 + 长连接（文档 2 第 3.12 节）。"""
    await websocket.accept()
    try:
        auth_message = await websocket.receive_json()
        if not isinstance(auth_message, dict) \
                or auth_message.get("type") != "auth":
            raise ValueError("首条消息必须是 auth")
        user_id = verify_token(str(auth_message.get("token") or ""))
    except WebSocketDisconnect:
        # 客户端在认证前就断开：页面刷新、uvicorn --reload 重启
        # （关闭码 1012 service restart）都会走这里。连接已不在，
        # 安静退出即可——不接住会穿透到 ASGI 层打 traceback
        # （2026-10-17 实测：--reload 每次重启都刷一脸）。
        logger.debug("WS 客户端在认证前断开")
        return
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        logger.info("WS 认证失败: %s", exc)
        try:
            await websocket.send_json({"type": "auth_error",
                                       "message": "认证失败"})
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError):
            pass  # 对方已走，发不出去也无所谓
        return

    try:
        await websocket.send_json({"type": "auth_ok"})
    except (WebSocketDisconnect, RuntimeError):
        logger.debug("WS 认证成功但客户端已断开")
        return
    ws_manager.connect(user_id, websocket)
    logger.info("WS 连接建立: %s", user_id)
    try:
        while True:
            # 客户端消息当前无语义（保活读）；断线由 receive 抛异常感知
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
        logger.info("WS 连接断开: %s", user_id)
    except Exception as exc:  # 其他异常同样清理连接
        ws_manager.disconnect(user_id, websocket)
        logger.warning("WS 连接异常断开: %s: %s", user_id, exc)

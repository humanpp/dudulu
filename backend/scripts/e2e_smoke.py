"""全链路 E2E 冒烟（Phase 10 10b-6：无浏览器自动化环境的备选方案）。

跑一个**真实的 uvicorn 进程**（真实 SQLite + 真实 YantrikDB + 真实
ant-ling LLM），用 httpx（HTTP）+ websockets（WS）客户端走完整链路：

  注册 → 登录(WS 认证) → 建角色 → 对话(非流式) → WS 收 state_update
  → 对话(流式 SSE) → 状态面板 → 角色列表新字段 → 记忆检索
  → 设置页改 LLM 配置(用户覆盖) → 再对话仍正常
  → 缩短心跳 + 播种孤独状态 → 调度器自动发主动消息 → WS 收到

用法（在 backend/ 目录）：
    python scripts/e2e_smoke.py

环境要求：.env 里的 DUDULU_LLM_API_KEY（真实 ant-ling）；httpx + websockets。
临时库路径由本脚本用环境变量注入（不碰 data/ 真实库）。
退出码：全部通过 0；任一步失败 1。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

BASE_URL = "http://127.0.0.1:{port}"
WS_URL = "ws://127.0.0.1:{port}/ws/events"
STARTUP_TIMEOUT = 30  # 秒

PASSED: list[str] = []
FAILED: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}" + (f" — {detail}" if detail else "")
    print(line, flush=True)
    (PASSED if ok else FAILED).append(name)


def start_server(port: int, tmp: Path) -> subprocess.Popen:
    """启动 uvicorn（临时库 + 假嵌入器 + 缩短的心跳）。"""
    env = dict(os.environ)
    env.update({
        "DUDULU_SQLITE_PATH": str(tmp / "e2e.db"),
        "DUDULU_YANTRIK_PATH": str(tmp / "e2e_memory.db"),
        "DUDULU_YANTRIK_EMBEDDER": "fake",   # E2E 不下载嵌入模型
        "DUDULU_SCHEDULER_ENABLED": "true",  # 主动消息走真实调度器
        "DUDULU_PROACTIVE_TICK_MINUTES": "0.05",  # 3 秒一跳
        "PYTHONUNBUFFERED": "1",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BACKEND_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process


def wait_ready(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(BASE_URL.format(port=port), timeout=2)
            return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def seed_proactive_trigger(db_path: Path, character_id: str, user_id: str
                           ) -> None:
    """播种主动触发条件：高孤独 + 5 小时没说话。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE character_user_state SET internal_state = ?, "
        "last_interaction = datetime('now', '-5 hours') "
        "WHERE character_id = ? AND user_id = ?",
        (json.dumps({"LON": 0.9, "ANT": 0.1}), character_id, user_id))
    conn.commit()
    conn.close()


async def receive_until(ws, predicate, timeout=30):
    """在超时内等待满足条件的 WS 帧。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        data = json.loads(raw)
        if predicate(data):
            return data


async def run_e2e(port: int, tmp: Path) -> None:
    base = BASE_URL.format(port=port)
    ws_url = WS_URL.format(port=port)
    db_path = tmp / "e2e.db"

    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        # 1. 注册
        username = f"e2e_{int(time.time())}"
        response = await client.post("/api/auth/register", json={
            "username": username, "password": "secret123"})
        report("注册", response.status_code == 200,
               f"status={response.status_code}")
        # 文档 2 第 6.1 节：注册/登录响应字段是 {user_id, token}
        token = response.json().get("token", "")
        headers = {"Authorization": f"Bearer {token}",
                   "X-Timezone": "Asia/Shanghai"}

        # 2. WS 连接 + 认证
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            auth_reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
            report("WS 认证", auth_reply.get("type") == "auth_ok",
                   str(auth_reply))

            # 3. 建角色（模板）
            response = await client.post("/api/character/create/template",
                                         json={"template_id": "gentle_artist",
                                               "name": "E2E 知夏"},
                                         headers=headers)
            character_id = response.json().get("id", "")
            report("创建角色", bool(character_id),
                   f"id={character_id[:8]}…")

            # 4. 非流式对话（真实 LLM）
            response = await client.post("/api/chat", json={
                "character_id": character_id, "session_id": "e2e-session",
                "message": "你好，请用一句话介绍你自己。"}, headers=headers)
            chat_ok = response.status_code == 200
            reply = response.json().get("reply", "") if chat_ok else ""
            report("非流式对话", chat_ok,
                   f"reply={reply[:40]!r}")

            # 5. WS 收到 state_update（裁决③契约字段）
            state_update = await receive_until(
                ws, lambda data: data.get("type") == "state_update")
            state_ok = bool(state_update) and all(
                key in state_update for key in
                ("expression", "internal", "affective", "delta"))
            report("WS state_update", state_ok,
                   f"stage={state_update.get('affective', {}).get('stage_code')}"
                   if state_update else "未收到")

            # 6. 流式对话（SSE \n\n 切分）
            tokens: list[str] = []
            done_payload = None
            async with client.stream(
                    "POST", "/api/chat/stream",
                    json={"character_id": character_id,
                          "session_id": "e2e-session",
                          "message": "今天天气怎么样？随便聊聊。"},
                    headers=headers) as stream:
                buffer = ""
                async for chunk in stream.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        for line in frame.splitlines():
                            if line.startswith("event: "):
                                event = line[7:]
                            elif line.startswith("data: "):
                                data = line[6:]
                                if event == "token":
                                    tokens.append(data)
                                elif event == "done":
                                    done_payload = json.loads(data)
            report("流式对话", bool(tokens) and done_payload is not None,
                   f"tokens={len(tokens)}")

            # 7. 状态查询
            response = await client.get(
                f"/api/state?character_id={character_id}", headers=headers)
            report("状态查询", response.status_code == 200
                   and "affective" in response.json())

            # 8. 角色列表新字段（Phase 10 裁决⑦）
            response = await client.get("/api/character/list",
                                        headers=headers)
            characters = response.json().get("characters", [])
            fields_ok = bool(characters) and all(
                field in characters[0] for field in
                ("occupation", "stage_code", "stage_label", "affection"))
            report("角色列表字段", fields_ok,
                   f"occupation={characters[0].get('occupation')!r}"
                   if characters else "空列表")

            # 9. 记忆检索
            response = await client.get(
                "/api/memory/search",
                params={"character_id": character_id, "q": "聊天"},
                headers=headers)
            report("记忆检索", response.status_code == 200
                   and "memories" in response.json())

            # 10. 设置页：改 LLM 配置（用户覆盖）→ GET 合并视图。
            # 用当前模型原样写回：验证覆盖链路生效，不换模型
            # （换成一个提供商不存在的模型会让真实调用 400，那不是本步要测的）。
            current = (await client.get("/api/config/llm",
                                        headers=headers)).json()
            response = await client.put("/api/config/llm", json={
                "mode": "api",
                "api": {"model": current["api"]["model"]}}, headers=headers)
            report("PUT LLM 配置", response.status_code == 200)
            response = await client.get("/api/config/llm", headers=headers)
            report("GET LLM 配置（用户覆盖）",
                   response.json()["api"]["model"] == current["api"]["model"],
                   f"model={current['api']['model']}")

            # 11. 改配置后再对话仍正常（即时生效）
            response = await client.post("/api/chat", json={
                "character_id": character_id, "session_id": "e2e-session",
                "message": "再说一句话。"}, headers=headers)
            report("覆盖后对话", response.status_code == 200)

            # 12. 主动消息：缩短心跳 + 播种孤独，等调度器推送
            await client.put("/api/config/proactive", json={
                "enabled": True, "interval_hours": 0.05,
                "min_gap_hours": 0.01, "max_daily": 10,
                "quiet_hours": [0, 0]}, headers=headers)
            seed_proactive_trigger(db_path, character_id,
                                   _user_id_of(client, token))
            proactive = await receive_until(
                ws, lambda data: data.get("type") == "proactive_message",
                timeout=45)
            report("主动消息推送", bool(proactive),
                   f"reason={proactive.get('reason')}" if proactive
                   else "45 秒内未收到（可能被频率门控/LLM 限流挡住）")

            # 13. 主动消息已入记忆（proactive_message 类型）
            if proactive:
                response = await client.get(
                    "/api/memory/search",
                    params={"character_id": character_id,
                            "q": proactive.get("message", "在")[:12]},
                    headers=headers)
                memories = response.json().get("memories", [])
                report("主动消息入记忆",
                       any(item.get("is_proactive") for item in memories),
                       f"命中 {len(memories)} 条")


def _user_id_of(client: httpx.AsyncClient, token: str) -> str:
    """从 JWT 取 sub（E2E 播种用）。"""
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    import base64

    return json.loads(base64.urlsafe_b64decode(padded))["sub"]


def main() -> int:
    port = 8931
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        server = start_server(port, tmp)
        try:
            if not wait_ready(port, STARTUP_TIMEOUT):
                report("服务启动", False, f"{STARTUP_TIMEOUT}s 超时")
                return 1
            report("服务启动", True)
            asyncio.run(run_e2e(port, tmp))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    if FAILED:
        print("失败项：", ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

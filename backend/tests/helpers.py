"""测试共享辅助：配置路径、文档 1 第 3.2 节示例档案、假 LLM 与假协作者。

本文件不是测试用例（不以 test_ 开头，pytest 不会收集）。
"""

import atexit
import json
import shutil
import tempfile
from pathlib import Path

from core.character.profile import CharacterProfile
from llm.base import LLMClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = str(BACKEND_DIR / "config" / "dudulu_config.yaml")
PROMPT_PATH = str(BACKEND_DIR / "config" / "prompts" / "classifier.txt")

# 文档 1 第 3.2 节的示例档案（林知夏）
LIN_ZHIXIA = {
    "id": "char_001",
    "name": "林知夏",
    "nickname": "知夏",
    "gender": "female",
    "age": 24,
    "birthday": "2002-03-14",
    "zodiac": "双鱼座",
    "appearance": {
        "height_cm": 165,
        "weight_kg": 50,
        "body_type": "slim",
        "hair": "黑色长发，微卷，常扎低马尾",
        "eyes": "深棕色，笑起来会弯成月牙",
        "skin": "白皙，脸颊有淡淡雀斑",
        "distinguishing": "左耳有一颗小痣",
        "style": "简约舒适，偏爱米色和浅蓝色",
        "avatar_url": "/assets/char_001.png",
    },
    "background": {
        "birthplace": "杭州",
        "education": "视觉传达设计专业",
        "occupation": "插画师，自由职业",
        "living_situation": "独居，养了一只叫年糕的橘猫",
        "family": "父母在老家，关系和睦但联系不频繁",
        "key_experiences": [
            "大学时曾休学一年去云南支教",
            "有过一段两年的感情，已和平分手",
            "去年开始做独立插画，收入不稳定但很满足",
        ],
        "current_state": "正在准备第一个个人画展，压力与期待并存",
    },
    "personality": {
        "big_five": {
            "openness": 0.82,
            "conscientiousness": 0.55,
            "extraversion": 0.40,
            "agreeableness": 0.75,
            "neuroticism": 0.45,
        },
        "traits": ["温柔", "敏感", "有主见", "偶尔固执", "善于倾听"],
        "flaws": ["容易内耗", "不擅长拒绝", "对批评敏感"],
        "values": ["真诚", "自由", "创造力", "被理解"],
        "fears": ["被忽视", "重复单调的生活", "辜负期待"],
    },
    "speech_style": {
        "tone": "温和、口语化、偶尔俏皮",
        "pace": "不急不缓，喜欢用短句",
        "vocabulary": "日常词汇为主，偶尔冒出画画相关的比喻",
        "catchphrases": ["嗯……让我想想", "这样啊", "其实我觉得吧"],
        "avoid": ["过度正式的表达", "网络烂梗", "说教语气"],
        "emoji_usage": "偶尔使用，偏好 🌿 ☁️ 🐱 这类柔和的",
        "punctuation": "喜欢用省略号，句尾偶尔加'呀''呢'",
    },
    "theme": {"primary": "#C4A882", "soft": "#E8DDD0", "deep": "#8B7355"},
    "relationship_defaults": {
        "initial_stage": "S1",
        "initial_affection": 25,
        "initial_trust": 30,
        "initial_attachment": 15,
        "first_meeting_context": "在某个线上绘画社区认识，聊过几次",
    },
    "emotion_overrides": {
        "initial_expression": {"PLE": 0.20, "ARO": -0.05, "DOM": -0.10,
                               "INT": 0.25, "SEC": 0.35},
        "initial_internal": {"ANX": 0.25, "LON": 0.35, "CUR": 0.60, "PAT": 0.65},
        "decay_modifiers": {
            "expression_half_life_hours": 5.0,
            "affective_half_life_days": {"AFF": 35, "TRS": 100, "ATT": 200},
        },
        "internal_to_expression_weights": {"ANX": 1.2, "VUL": 1.1},
        "event_sensitivity": {"conflict": 1.3, "vulnerable": 1.1},
        "proactive": {
            "interval_hours": 4.5,
            "min_gap_hours": 3.0,
            "max_daily": 3,
            "silence_threshold": 3,
            "silence_recovery_hours": 12,
            "eagerness": 1.0,
        },
    },
    "proactive_enabled": True,
    "visibility": {"is_public": False, "owner_user_id": "user_001"},
    "generation_meta": {
        "source": "template",
        "template_id": "gentle_artist",
        "random_seed": 42,
        "locked_fields": [],
        "created_at": "2026-10-14T10:00:00",
    },
}


def make_profile(profile_id: str = "char_test", **sections) -> CharacterProfile:
    """构造角色档案；sections 直接合进档案 dict。"""
    data = {"id": profile_id, "name": "测试角色"}
    data.update(sections)
    return CharacterProfile.from_dict(data)


# ── 数据库测试辅助 ──────────────────────────────────────────

def make_db_session_factory(sqlite_path: str | Path):
    """按路径建库并返回 session 工厂（测试用，与 db.sqlite 同参数）。"""
    from db.sqlite import create_session_factory, init_db

    factory = create_session_factory(str(sqlite_path))
    init_db(factory.kw["bind"])
    return factory


def temp_db_factory():
    """在系统临时目录建独立 SQLite 库，进程退出时清理（测试默认用）。"""
    directory = Path(tempfile.mkdtemp(prefix="dudulu_test_"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    return make_db_session_factory(directory / "test.db")


def seed_user(factory, username: str = "tester",
              password: str = "secret123") -> str:
    """建一个用户并返回 user_id（密码经 bcrypt 哈希）。"""
    from auth.password import hash_password
    from db.models import User
    from memory.schemas import generate_id

    with factory() as db:
        user = User(id=generate_id(), username=username,
                    password_hash=hash_password(password))
        db.add(user)
        db.commit()
        return user.id


def seed_character(factory, character_id: str = "char_001",
                   name: str = "测试角色", **sections) -> str:
    """建一个角色行并返回 character_id（Phase 6 起路由/管道要求角色真实存在）。"""
    import json

    from core.character.profile import CharacterProfile
    from db.models import Character

    profile = CharacterProfile.from_dict(
        {"id": character_id, "name": name, **sections})
    with factory() as db:
        row = Character(id=character_id, name=name)
        row.profile_json = json.dumps(profile.to_dict(), ensure_ascii=False)
        row.generation_meta_json = json.dumps(
            profile.generation_meta.__dict__, ensure_ascii=False, default=str)
        row.owner_user_id = profile.visibility.owner_user_id
        row.is_public = 0
        row.is_active = 1
        row.is_archived = 0
        db.add(row)
        db.commit()
    return character_id


def build_test_app(session_factory, pipeline=None, memory_manager=None,
                   settings=None):
    """装配测试用 FastAPI 应用：挂全部路由 + 覆盖会话工厂（可选覆盖管道/记忆库/配置）。"""
    from fastapi import FastAPI

    from api.routes_auth import router as auth_router
    from api.routes_character import router as character_router
    from api.routes_chat import router as chat_router
    from api.routes_chat import get_pipeline
    from api.routes_config import router as config_router
    from api.routes_memory import router as memory_router
    from api.routes_message import router as message_router
    from api.routes_session import router as session_router
    from api.routes_state import router as state_router
    from api.routes_template import router as template_router
    from api.routes_ws import router as ws_router
    from db.sqlite import get_session_factory
    from memory.memory_manager import memory_manager_dependency

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.include_router(session_router, prefix="/api")
    app.include_router(message_router, prefix="/api")
    app.include_router(state_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(character_router, prefix="/api")
    app.include_router(template_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(chat_router, prefix="/api")
    app.include_router(ws_router)  # WS /ws/events 不带前缀
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    if pipeline is not None:
        app.dependency_overrides[get_pipeline] = lambda: pipeline
    if memory_manager is not None:
        app.dependency_overrides[memory_manager_dependency] = \
            lambda: memory_manager
    if settings is not None:
        from config.settings import get_settings

        app.dependency_overrides[get_settings] = lambda: settings
    return app


def make_memory_manager(tmp_path, embedder: str = "fake"):
    """测试用 MemoryManager：临时 SQLite + 临时 YantrikDB + 假嵌入器。

    embedder="fake" 无网络、确定性；embedder="default" 走捆绑模型
    （模型已缓存时 ~0.3s，仅集成冒烟用）。
    """
    from memory.memory_manager import MemoryManager

    factory = make_db_session_factory(tmp_path / "dudulu.db")
    manager = MemoryManager(str(tmp_path / "dudulu_memory.db"),
                            db_session_factory=factory, embedder=embedder)
    return manager


def auth_header(user_id: str) -> dict:
    """带 Bearer Token 的请求头。"""
    from auth.jwt import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


def build_probe_app(session_factory):
    """带 GET /whoami 的探针应用：返回当前用户 id/username/timezone。

    用于认证依赖（get_current_user）与 X-Timezone 同步的 HTTP 级测试；
    会话工厂已覆盖到临时库，不会碰真实 data/dudulu.db。
    """
    from fastapi import Depends, FastAPI

    from auth.dependencies import get_current_user
    from db.models import User
    from db.sqlite import get_session_factory

    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: User = Depends(get_current_user)) -> dict:
        return {"id": user.id, "username": user.username, "tz": user.timezone}

    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return app


class FakeLLM(LLMClient):
    """脚本化 LLM：按 prompt 内容区分分类器调用与生成调用，并记录全部 prompt。"""

    def __init__(self, classifier_event=None, reply="听见你了。",
                 tokens=("听", "见", "你", "了。"), stream_error=None):
        self.classifier_event = classifier_event or {
            "label": "affectionate", "intensity": 0.6, "busy_signal": False}
        self.reply = reply
        self.tokens = tokens
        self.stream_error = stream_error
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        if "事件分类器" in prompt:
            return json.dumps(self.classifier_event, ensure_ascii=False)
        return self.reply

    async def generate_stream(self, prompt: str, **kwargs):
        for index, token in enumerate(self.tokens):
            if self.stream_error and index >= 1:
                raise self.stream_error
            yield token

    async def embed(self, text: str) -> list[float]:
        # 与真实客户端一致：无嵌入端点（测试不需要嵌入）
        raise NotImplementedError("FakeLLM 不提供嵌入（测试用）")


class SpyMemoryManager:
    """记录 store_conversation 调用；retrieve 恒返回空；get_memory 恒 None。"""

    def __init__(self):
        self.stored: list = []
        self.retrieve_calls: list[dict] = []

    def retrieve(self, query, character_id, user_id, current_state,
                 emotion_tag=None, time_range=None, top_k=5) -> list:
        self.retrieve_calls.append({
            "query": query, "character_id": character_id, "user_id": user_id,
            "current_state": current_state, "top_k": top_k})
        return []

    def store_conversation(self, turn, session_id=None) -> None:
        self.stored.append((turn, session_id))

    def get_memory(self, rid: str):
        return None


class SpySedimenter:
    def __init__(self):
        self.calls: list[tuple] = []

    def maybe_sediment(self, character_id: str, user_id: str) -> None:
        self.calls.append((character_id, user_id))


class SpyProactiveState:
    def __init__(self):
        self.cleared: list[tuple] = []
        self.busy: list[tuple] = []
        self.replies: list[tuple] = []

    def clear_busy_until(self, character_id: str, user_id: str) -> None:
        self.cleared.append((character_id, user_id))

    def set_busy_until(self, character_id: str, user_id: str, hours: float) -> None:
        self.busy.append((character_id, user_id, hours))

    def register_user_reply(self, character_id: str, user_id: str) -> None:
        self.replies.append((character_id, user_id))


class FakeCharacterManager:
    def __init__(self, profile):
        self.profile = profile
        self.calls: list[str] = []

    def get(self, character_id: str):
        self.calls.append(character_id)
        return self.profile


class FakeWSManager:
    def __init__(self):
        self.pushed: list[tuple] = []

    def push(self, user_id: str, message: dict) -> None:
        self.pushed.append((user_id, message))

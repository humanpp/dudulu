"""配置接口单元测试（文档 2 第 6.10 节）。"""

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from helpers import auth_header, build_test_app, make_db_session_factory, seed_user

# 隔离 .env 里的真实联调 key：测试一律用空配置的 Settings
TEST_SETTINGS = Settings(_env_file=None)


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory, settings=TEST_SETTINGS)


@pytest.fixture
def user_id(factory):
    return seed_user(factory)


@pytest.fixture
def other_user_id(factory, user_id):
    return seed_user(factory, username="other", password="secret123")


class TestGetLLMConfig:
    def test_returns_masked_shape(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/config/llm",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"mode", "api", "local"}
        assert payload["mode"] == "api"
        # 不返回明文 key，只有 has_api_key / api_key_masked
        assert "api_key" not in payload["api"]
        assert payload["api"]["has_api_key"] is False
        assert payload["api"]["api_key_masked"] is None
        assert payload["local"]["base_url"]

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get("/api/config/llm").status_code == 401

    def test_masks_configured_key(self, app, user_id, factory):
        from db.config_store import LLM_CONFIG_KEY, save_config

        with factory() as db:
            save_config(db, LLM_CONFIG_KEY,
                        {"api": {"api_key": "sk-studio-abcdef123456"}})
            db.commit()
        # 覆盖后的 key 也要掩码（只露前 3 位）
        with TestClient(app) as client:
            payload = client.get("/api/config/llm",
                                 headers=auth_header(user_id)).json()
        assert payload["api"]["has_api_key"] is True
        assert payload["api"]["api_key_masked"].startswith("sk-")
        assert "abcdef" not in payload["api"]["api_key_masked"]


class TestUpdateLLMConfig:
    def test_partial_update_persists(self, app, user_id, factory):
        with TestClient(app) as client:
            response = client.put("/api/config/llm",
                                  json={"mode": "local",
                                        "local": {"model": "qwen-test"}},
                                  headers=auth_header(user_id))
            assert response.json() == {"status": "ok"}
            payload = client.get("/api/config/llm",
                                 headers=auth_header(user_id)).json()
        assert payload["mode"] == "local"
        assert payload["local"]["model"] == "qwen-test"
        assert payload["local"]["base_url"]  # 未传的字段保持

    def test_clear_key_with_empty_string(self, app, user_id, factory):
        from db.config_store import LLM_CONFIG_KEY, load_config, save_config

        with factory() as db:
            save_config(db, LLM_CONFIG_KEY,
                        {"api": {"api_key": "sk-old"}})
            db.commit()
        with TestClient(app) as client:
            client.put("/api/config/llm",
                       json={"api": {"api_key": ""}},
                       headers=auth_header(user_id))
            payload = client.get("/api/config/llm",
                                 headers=auth_header(user_id)).json()
        with factory() as db:
            # 清除写进**用户**覆盖（不动全局默认）
            stored = load_config(db, LLM_CONFIG_KEY, user_id=user_id)
            global_default = load_config(db, LLM_CONFIG_KEY)
        assert stored["api"]["api_key"] == ""   # 显式清除被记住
        assert global_default["api"]["api_key"] == "sk-old"  # 全局不动
        assert payload["api"]["has_api_key"] is False

    def test_missing_fields_keep_original(self, app, user_id, factory):
        """不带 api_key 字段 = 保持原值（不清除）。"""
        from db.config_store import LLM_CONFIG_KEY, save_config

        with factory() as db:
            save_config(db, LLM_CONFIG_KEY,
                        {"api": {"api_key": "sk-keepme", "model": "m1"}})
            db.commit()
        with TestClient(app) as client:
            client.put("/api/config/llm", json={"mode": "api"},
                       headers=auth_header(user_id))
            payload = client.get("/api/config/llm",
                                 headers=auth_header(user_id)).json()
        assert payload["api"]["has_api_key"] is True
        assert payload["api"]["model"] == "m1"

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.put("/api/config/llm", json={"mode": "api"}) \
                .status_code == 401


class TestGetProactiveConfig:
    def test_returns_documented_fields(self, app, user_id):
        with TestClient(app) as client:
            payload = client.get("/api/config/proactive",
                                 headers=auth_header(user_id)).json()
        assert set(payload) == {"enabled", "interval_hours", "min_gap_hours",
                                "max_daily", "silence_threshold",
                                "silence_recovery_hours", "quiet_hours"}
        # 默认值来自 dudulu_config.yaml（文档 1 第 12.4 节参数表）
        assert payload["interval_hours"] == 4.5
        assert payload["min_gap_hours"] == 3.0
        assert payload["max_daily"] == 3
        assert payload["silence_threshold"] == 3
        assert payload["silence_recovery_hours"] == 12
        assert payload["quiet_hours"] == [23, 8]
        assert payload["enabled"] is True

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get("/api/config/proactive").status_code == 401


class TestUpdateProactiveConfig:
    def test_partial_update(self, app, user_id):
        with TestClient(app) as client:
            assert client.put("/api/config/proactive",
                              json={"enabled": False},
                              headers=auth_header(user_id)).json() == \
                {"status": "ok"}
            payload = client.get("/api/config/proactive",
                                 headers=auth_header(user_id)).json()
        assert payload["enabled"] is False
        assert payload["interval_hours"] == 4.5  # 未传的字段保持

    def test_update_quiet_hours(self, app, user_id):
        with TestClient(app) as client:
            client.put("/api/config/proactive",
                       json={"quiet_hours": [22, 7]},
                       headers=auth_header(user_id))
            payload = client.get("/api/config/proactive",
                                 headers=auth_header(user_id)).json()
        assert payload["quiet_hours"] == [22, 7]

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.put("/api/config/proactive",
                              json={"enabled": False}).status_code == 401


class TestUserScoping:
    """Phase 10 额外问题 1：配置按 user 分区，互不影响。"""

    def test_llm_override_isolated_between_users(self, app, user_id,
                                                 other_user_id):
        """A 配了自己的 Key：B 看到的是未配置（不是 A 的 Key）。"""
        with TestClient(app) as client:
            client.put("/api/config/llm",
                       json={"mode": "api", "api": {"api_key": "sk-user-a",
                                                    "model": "model-a"}},
                       headers=auth_header(user_id))
            mine = client.get("/api/config/llm",
                              headers=auth_header(user_id)).json()
            theirs = client.get("/api/config/llm",
                                headers=auth_header(other_user_id)).json()
        assert mine["api"]["has_api_key"] is True
        assert mine["api"]["model"] == "model-a"
        # B 的合并视图只有全局默认（测试 Settings 无 key）→ 未配置
        assert theirs["api"]["has_api_key"] is False
        assert theirs["api"]["model"] == "gpt-4o"  # Settings 默认

    def test_proactive_override_isolated_between_users(self, app, user_id,
                                                       other_user_id):
        """A 关掉主动行为：B 不受影响。"""
        with TestClient(app) as client:
            client.put("/api/config/proactive", json={"enabled": False},
                       headers=auth_header(user_id))
            mine = client.get("/api/config/proactive",
                              headers=auth_header(user_id)).json()
            theirs = client.get("/api/config/proactive",
                                headers=auth_header(other_user_id)).json()
        assert mine["enabled"] is False
        assert theirs["enabled"] is True

    def test_global_default_visible_to_all(self, app, user_id, other_user_id,
                                           factory):
        """__global__ 全局默认对所有用户可见（用户没改就用全局的）。"""
        from db.config_store import (
            GLOBAL_CONFIG_USER,
            PROACTIVE_CONFIG_KEY,
            save_config,
        )

        with factory() as db:
            save_config(db, PROACTIVE_CONFIG_KEY, {"max_daily": 9},
                        user_id=GLOBAL_CONFIG_USER)
            db.commit()
        with TestClient(app) as client:
            mine = client.get("/api/config/proactive",
                              headers=auth_header(user_id)).json()
            theirs = client.get("/api/config/proactive",
                                headers=auth_header(other_user_id)).json()
        assert mine["max_daily"] == 9
        assert theirs["max_daily"] == 9

    def test_user_override_beats_global_default(self, app, user_id, factory):
        """用户覆盖优先于全局默认。"""
        from db.config_store import (
            GLOBAL_CONFIG_USER,
            PROACTIVE_CONFIG_KEY,
            save_config,
        )

        with factory() as db:
            save_config(db, PROACTIVE_CONFIG_KEY, {"max_daily": 9},
                        user_id=GLOBAL_CONFIG_USER)
            db.commit()
        with TestClient(app) as client:
            client.put("/api/config/proactive", json={"max_daily": 2},
                       headers=auth_header(user_id))
            payload = client.get("/api/config/proactive",
                                 headers=auth_header(user_id)).json()
        assert payload["max_daily"] == 2

    def test_partial_update_keeps_other_fields(self, app, user_id):
        """部分更新只改传的字段，其余保持（含全局默认部分）。"""
        with TestClient(app) as client:
            client.put("/api/config/proactive",
                       json={"interval_hours": 2.0},
                       headers=auth_header(user_id))
            payload = client.get("/api/config/proactive",
                                 headers=auth_header(user_id)).json()
        assert payload["interval_hours"] == 2.0
        assert payload["min_gap_hours"] == 3.0  # 未传，保持
        assert payload["quiet_hours"] == [23, 8]

"""模板加载器（文档 2 第 3.4 节 + 文档 1 第 4.2 / 4.5 节）。

职责：从 templates/ 目录加载模板 YAML，解析 extends 继承链（深合并，
列表整体替换），按 id 缓存；提供内置/社区模板清单；并承载姓名池与
随机池（names/ 与 pools/）的读取——两者都是角色包的静态数据。

默认路径取本文件所在目录（backend/core/character/），与运行 cwd 无关；
配置里的 templates.directory 是相对仓库根的路径，从 backend/ 启动时不可用，
故不采用（已在交付报告中说明）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CHARACTER_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_DIR = CHARACTER_DIR / "templates"
DEFAULT_NAMES_DIR = CHARACTER_DIR / "names"
DEFAULT_POOLS_DIR = CHARACTER_DIR / "pools"

# 数值配置文件（读取 templates.builtin 的顺序清单）
CONFIG_PATH = str(CHARACTER_DIR.parents[1] / "config" / "dudulu_config.yaml")


@dataclass
class TemplateMeta:
    """模板清单项（文档 2 第 3.4 节 list_builtin/list_community 的返回元素）。

    Phase 10 起带 `theme` 与 `defaults`：创建向导（文档 3 第 9.7 节）
    选中模板后直接用它们初始化档案，不再为拿默认值多打一次详情接口。
    """

    id: str
    name: str
    description: str = ""
    icon: str = ""
    tags: list[str] = field(default_factory=list)
    theme: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """输出 API 用的 dict 形态；副作用：无。"""
        return {"id": self.id, "name": self.name,
                "description": self.description, "icon": self.icon,
                "tags": list(self.tags), "theme": dict(self.theme),
                "defaults": _deep_copy(self.defaults)}


@dataclass
class Template:
    """角色模板（文档 1 第 4.2 节结构）。"""

    id: str
    name: str
    description: str = ""
    icon: str = ""
    tags: list[str] = field(default_factory=list)
    theme: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    locked_fields: list[str] = field(default_factory=list)
    extends: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        """从 YAML dict 还原模板；副作用：无。"""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            tags=list(data.get("tags") or []),
            theme=dict(data.get("theme") or {}),
            defaults=dict(data.get("defaults") or {}),
            locked_fields=list(data.get("locked_fields") or []),
            extends=data.get("extends"),
        )

    def to_dict(self) -> dict:
        """输出完整模板 dict（导出模板用）；副作用：无。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "tags": list(self.tags),
            "theme": dict(self.theme),
            "defaults": _deep_copy(self.defaults),
            "locked_fields": list(self.locked_fields),
            **({"extends": self.extends} if self.extends else {}),
        }

    def meta(self) -> TemplateMeta:
        """输出清单项（含 theme/defaults，供向导直接初始化档案）。"""
        return TemplateMeta(id=self.id, name=self.name,
                            description=self.description, icon=self.icon,
                            tags=list(self.tags), theme=dict(self.theme),
                            defaults=_deep_copy(self.defaults))


def _deep_copy(data: dict) -> dict:
    """JSON 兼容结构的深拷贝（模板数据全是 dict/list/标量）。"""
    import copy

    return copy.deepcopy(data)


def deep_merge(parent: dict, child: dict) -> dict:
    """深合并：child 覆盖 parent，dict 递归、列表与标量整体替换。

    输入：父 dict、子 dict；输出：合并后的新 dict；副作用：无。
    """
    merged = _deep_copy(parent)
    for key, value in child.items():
        if (key in merged and isinstance(merged[key], dict)
                and isinstance(value, dict)):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = _deep_copy(value) if isinstance(value, (dict, list)) \
                else value
    return merged


class TemplateLoader:
    """职责：加载模板，支持继承和覆盖（文档 2 第 3.4 节）。"""

    def __init__(self, template_dir: str | Path = DEFAULT_TEMPLATE_DIR):
        """初始化加载器。

        输入：模板目录（默认 backend/core/character/templates）；
        副作用：无（懒加载，首次 load 才读盘）。
        """
        self.template_dir = Path(template_dir)
        self._cache: dict[str, Template] = {}

    def load(self, template_id: str) -> Template:
        """加载模板，解析继承链（带缓存）。

        输入：模板 id；输出：Template；副作用：读 YAML 文件并缓存。
        模板不存在抛 FileNotFoundError。
        """
        if template_id in self._cache:
            return self._cache[template_id]
        raw = self._read_yaml(template_id)
        if "extends" in raw:
            parent = self.load(raw["extends"])
            raw = deep_merge(parent.to_dict(), raw)
        template = Template.from_dict(raw)
        self._cache[template_id] = template
        return template

    def _read_yaml(self, template_id: str) -> dict:
        """读模板 YAML 文件；不存在抛 FileNotFoundError。"""
        path = self.template_dir / f"{template_id}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"模板不存在: {template_id} ({path})")
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        template = data.get("template")
        if not isinstance(template, dict) or "id" not in template:
            raise ValueError(f"模板文件缺少 template.id: {path}")
        return template

    def list_builtin(self) -> list[TemplateMeta]:
        """内置模板清单（按配置 templates.builtin 的顺序，未列的排后）。"""
        return self._list_meta(self._builtin_ids())

    def list_community(self) -> list[TemplateMeta]:
        """社区模板清单（导出产生的、不在内置清单中的模板）。"""
        builtin = set(self._builtin_ids())
        others = [path.stem for path in self._template_files()
                  if path.stem not in builtin]
        return self._list_meta(sorted(others))

    def _list_meta(self, template_ids: list[str]) -> list[TemplateMeta]:
        """按 id 列表加载模板元信息（单个失败不影响其余）。"""
        metas: list[TemplateMeta] = []
        for template_id in template_ids:
            try:
                metas.append(self.load(template_id).meta())
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("跳过无法加载的模板 %s: %s", template_id, exc)
        return metas

    def _template_files(self) -> list[Path]:
        """模板目录下的全部 .yaml 文件（按名排序）。"""
        if not self.template_dir.is_dir():
            return []
        return sorted(self.template_dir.glob("*.yaml"))

    def _builtin_ids(self) -> list[str]:
        """配置里的内置模板 id 顺序；配置不可读时退回文件列表。"""
        try:
            with open(CONFIG_PATH, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
            builtin = list((config.get("templates") or {}).get("builtin") or [])
            if builtin:
                return builtin
        except OSError as exc:
            logger.warning("读取模板配置失败，退回目录扫描: %s", exc)
        return [path.stem for path in self._template_files()]

    def save_template(self, template: Template) -> Path:
        """把模板写入模板目录（社区导出用）。

        输入：Template；输出：写入的文件路径；副作用：写 YAML 文件并刷新缓存。
        """
        self.template_dir.mkdir(parents=True, exist_ok=True)
        path = self.template_dir / f"{template.id}.yaml"
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump({"template": template.to_dict()}, handle,
                           allow_unicode=True, sort_keys=False)
        self._cache[template.id] = template
        return path


# ── 姓名池与随机池（names/ 与 pools/ 静态数据） ──────────────

def load_name_pool(gender: str,
                   names_dir: str | Path = DEFAULT_NAMES_DIR) -> list[str]:
    """按性别读姓名池（names/{male,female,neutral}.txt，每行一个名字）。

    输入：性别（male/female/non_binary，其余按 neutral）；输出：名字列表；
    副作用：读文件。文件不存在返回空列表。
    """
    filename = {"male": "male.txt", "female": "female.txt",
                "non_binary": "neutral.txt"}.get(gender, "neutral.txt")
    path = Path(names_dir) / filename
    if not path.is_file():
        logger.warning("姓名池不存在: %s", path)
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def load_pool(pool_name: str,
              pools_dir: str | Path = DEFAULT_POOLS_DIR) -> dict:
    """读随机池 YAML（pools/{pool_name}.yaml 的顶层结构）。

    输入：池名（不含扩展名）；输出：池 dict；副作用：读文件。
    """
    path = Path(pools_dir) / f"{pool_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"随机池不存在: {pool_name} ({path})")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_occupation_rules(
    pools_dir: str | Path = DEFAULT_POOLS_DIR) -> dict[str, int]:
    """职业 → 最低年龄映射（pools/occupations.yaml 中带 min_age 的条目）。

    输入：池目录；输出：{职业名: 最低年龄}；副作用：读文件。
    供一致性校验的"年龄 vs 职业"规则使用。
    """
    data = load_pool("occupations", pools_dir)
    rules: dict[str, int] = {}
    for entries in data.values():
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("min_age") is not None:
                rules[entry["name"]] = int(entry["min_age"])
    return rules

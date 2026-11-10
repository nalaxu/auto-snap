"""设置（外部面板 M4）：读写 config/settings.toml 的常用项，用 tomlkit 保留注释与格式。

只开放 SCHEMA 里列出的键；密钥只报告「是否已配置」，不返回内容、不在面板里修改（放 .env）。
修改在下次启动机器人时生效（运行中的机器人已经读过配置）。写入前备份到 logs/settings_backup/。
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

KEEP_BACKUPS = 20
EFFORTS = ["low", "medium", "high"]

# path, 类型, 标签, 说明, 额外约束
SCHEMA: list[dict[str, Any]] = [
    {"group": "游戏", "path": "game.client", "type": "client", "label": "默认客户端",
     "help": "auto = 第一个正在运行的客户端；启动面板「出征准备」也可以临时选"},
    {"group": "决策", "path": "brain.decider", "type": "select", "options": ["llm", "rule_based"], "label": "决策器",
     "help": "llm：大模型逐回合决策，超时 / 不合法由规则 AI 兜底；rule_based：只用规则 AI"},
    {"group": "决策", "path": "brain.snap", "type": "bool", "label": "允许 Snap", "help": "由大模型判断加注时机；规则 AI 兜底的回合从不 Snap"},
    {"group": "决策", "path": "brain.retreat", "type": "bool", "label": "允许撤退",
     "help": "只在对手刚 Snap 的回合和最后一回合考虑，按校准胜率算期望魔方"},
    {"group": "决策", "path": "brain.retreat_win_prob", "type": "float", "optional": True, "min": 0.05, "max": 0.95,
     "label": "固定撤退胜率", "help": "仅排位（竞技场看下方「竞技场」分组）。留空 = 按期望魔方计算（通常约 25%）；填了则胜率低于它就撤退"},
    {"group": "竞技场", "path": "arena.retreat_final", "type": "float", "min": 0.05, "max": 0.95, "label": "撤退线（正常）",
     "help": "最后一回合校准胜率低于它就撤退（0.3 = 30%）"},
    {"group": "竞技场", "path": "arena.retreat_opp_snap", "type": "float", "min": 0.05, "max": 0.95,
     "label": "撤退线（对手 Snap 当回合）", "help": "对手刚 Snap 的回合校准胜率低于它就撤退（0.45 = 45%）"},
    {"group": "竞技场", "path": "arena.retreat_opp_snap_from", "type": "int", "min": 1, "max": 7,
     "label": "对手 Snap 撤退线起始回合", "help": "更早的 Snap 多为诈唬，只按整场期望线判断"},
    {"group": "大模型", "path": "llm.profile", "type": "profile", "optional": True, "label": "厂商配置",
     "help": "留空 = 用 [llm] 默认配置；选 deepseek 等会用 [llm.profiles.*] 覆盖（环境变量 LLM_PROFILE 优先）"},
    {"group": "大模型", "path": "llm.provider", "type": "select", "options": ["openai", "anthropic"], "label": "协议",
     "help": "openai 含所有兼容 OpenAI 协议的中转 / 厂商"},
    {"group": "大模型", "path": "llm.api", "type": "select", "options": ["auto", "responses", "chat"], "label": "接口",
     "help": "auto：先试 Responses，不支持则回退 Chat Completions"},
    {"group": "大模型", "path": "llm.tasks.decision.model", "type": "str", "label": "决策模型", "help": "逐回合决策用的模型名"},
    {"group": "大模型", "path": "llm.tasks.decision.effort_timed", "type": "select", "options": EFFORTS, "label": "推理强度（有回合计时）",
     "help": "实测 sol low 中位约 10s，medium 约 17s，容易超时"},
    {"group": "大模型", "path": "llm.tasks.decision.effort", "type": "select", "options": EFFORTS, "label": "推理强度（无计时）",
     "help": "新手局等没有回合计时的对局"},
    {"group": "大模型", "path": "llm.tasks.decision.verbosity", "type": "select", "options": ["low", "medium", "high"],
     "label": "输出详略", "help": "low 实测中位 7.9→6.6s，质量不变"},
    {"group": "时间", "path": "llm.tasks.decision.deadline_timed_s", "type": "float", "min": 5, "max": 60, "label": "前期截止（秒）",
     "help": "有计时的 T1 到「后期起始回合」前，最多等大模型多久"},
    {"group": "时间", "path": "llm.tasks.decision.deadline_late_s", "type": "float", "min": 5, "max": 80, "label": "后期截止（秒）",
     "help": "后期回合场上牌多、模型更慢"},
    {"group": "时间", "path": "llm.tasks.decision.late_from_turn", "type": "int", "min": 2, "max": 7, "label": "后期起始回合", "help": ""},
    {"group": "时间", "path": "llm.tasks.decision.deadline_s", "type": "float", "min": 5, "max": 120, "label": "无计时截止（秒）", "help": ""},
    {"group": "时间", "path": "llm.tasks.decision.timer_reserve_s", "type": "float", "min": 2, "max": 20, "label": "执行预留（秒）",
     "help": "读到回合倒计时时：大模型截止 = min(上面的截止, 剩余 − 预留)；留给拖牌和结束回合"},
    {"group": "时间", "path": "llm.tasks.decision.min_llm_s", "type": "float", "min": 1, "max": 20, "label": "最少思考时间（秒）",
     "help": "剩余预算低于它就不问大模型，直接规则 AI"},
]
BY_PATH = {f["path"]: f for f in SCHEMA}


def _get(doc: Any, path: str) -> Any:
    cur = doc
    for k in path.split("."):
        if not hasattr(cur, "get") or k not in cur:
            return None
        cur = cur[k]
    return cur.unwrap() if hasattr(cur, "unwrap") else cur


def _set(doc: Any, path: str, value: Any) -> None:
    keys = path.split(".")
    cur = doc
    for k in keys[:-1]:
        if k not in cur:
            cur[k] = tomlkit.table()
        cur = cur[k]
    if value is None:
        if keys[-1] in cur:
            del cur[keys[-1]]
    else:
        cur[keys[-1]] = value


def _env_file(root: Path) -> dict[str, str]:
    out = {}
    p = root / ".env"
    if p.exists():
        for line in p.read_text("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Settings:
    def __init__(self, path: Path, root: Path, backup_dir: Path):
        self.path = path
        self.root = root
        self.backup_dir = backup_dir

    def load(self) -> tomlkit.TOMLDocument:
        return tomlkit.parse(self.path.read_text("utf-8"))

    def read(self) -> dict:
        doc = self.load()
        clients = list((_get(doc, "game.clients") or {}).keys())
        profiles = list((_get(doc, "llm.profiles") or {}).keys())
        fields = []
        for f in SCHEMA:
            opts = f.get("options")
            if f["type"] == "client":
                opts = ["auto", *clients]
            elif f["type"] == "profile":
                opts = profiles
            fields.append({**f, "options": opts, "value": _get(doc, f["path"])})
        return {"fields": fields, "secrets": self.secrets(doc)}

    def secrets(self, doc=None) -> list[dict]:
        """各密钥环境变量是否已配置（.env 或系统环境变量）；不返回内容。"""
        doc = doc or self.load()
        env = _env_file(self.root)
        names: list[tuple[str, str]] = []
        llm = _get(doc, "llm") or {}
        for label, cfg in [("默认", llm), *[(f"配置 {k}", v) for k, v in (llm.get("profiles") or {}).items()]]:
            for key in ("api_key_env", "base_url_env"):
                if cfg.get(key):
                    names.append((label, cfg[key]))
        names.append(("Claude", "ANTHROPIC_API_KEY"))
        seen, out = set(), []
        for label, n in names:
            if n in seen:
                continue
            seen.add(n)
            out.append({"name": n, "used_by": label, "set": bool(os.environ.get(n) or env.get(n))})
        return out

    @staticmethod
    def coerce(f: dict, v: Any) -> Any:
        if v in (None, "") and f.get("optional"):
            return None
        t = f["type"]
        try:
            if t == "bool":
                if not isinstance(v, bool):
                    raise ValueError
                return v
            if t == "int":
                v = int(v)
            elif t == "float":
                v = round(float(v), 3)
                if v == int(v) and f["path"].endswith("_s"):
                    v = int(v)
            elif t in ("str", "select", "client", "profile"):
                v = str(v).strip()
                if not v:
                    raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(400, f"「{f['label']}」的值不对") from None
        if t in ("int", "float") and not (f.get("min", float("-inf")) <= v <= f.get("max", float("inf"))):
            raise HTTPException(400, f"「{f['label']}」要在 {f.get('min')}–{f.get('max')} 之间")
        if t == "select" and v not in f["options"]:
            raise HTTPException(400, f"「{f['label']}」只能是 {', '.join(f['options'])}")
        return v

    def write(self, values: dict[str, Any]) -> dict:
        doc = self.load()
        clients = list((_get(doc, "game.clients") or {}).keys())
        profiles = list((_get(doc, "llm.profiles") or {}).keys())
        changed = []
        for path, raw in values.items():
            f = BY_PATH.get(path)
            if f is None:
                raise HTTPException(400, f"不支持修改 {path}")
            v = self.coerce(f, raw)
            if f["type"] == "client" and v not in ["auto", *clients]:
                raise HTTPException(400, f"没有客户端 {v}")
            if f["type"] == "profile" and v is not None and v not in profiles:
                raise HTTPException(400, f"没有厂商配置 {v}")
            if _get(doc, path) != v:
                _set(doc, path, v)
                changed.append(path)
        if changed:
            self._backup()
            text = tomlkit.dumps(doc)
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            tmp.write_text(text, "utf-8")
            os.replace(tmp, self.path)
        return {"changed": changed}

    def _backup(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, self.backup_dir / f"settings.toml.{datetime.now():%Y%m%d_%H%M%S_%f}")
        for old in sorted(self.backup_dir.glob("settings.toml.*"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)


class SettingsBody(BaseModel):
    values: dict[str, Any]


def create_router(s: Settings) -> APIRouter:
    r = APIRouter(prefix="/api/settings")
    r.get("")(s.read)

    @r.put("")
    def put(body: SettingsBody):
        return s.write(body.values)

    return r

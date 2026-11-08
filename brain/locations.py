"""场地评估：每个场地「本回合是否利好我们放牌」。

来源优先级：
1. 知识库 data/locations.json（人工确认 / 截图 / 赛后复盘写入）
2. 全量资料库 data/location_db.json（tools/update_card_db.py 抓取）：只有效果文字，bias 中性，source="db"
3. 大模型分析（brain/llm.py，有 API 凭据时后台异步执行，结果写回知识库）
3. 都没有 -> 中性（bias 0），并记录到 data/unknown_locations.json 供赛后复盘

bias 按回合阶段给：early = 第 1-3 回合，mid = 第 4-5 回合，final = 最后一回合；取值 -3..+3。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
KB_PATH = DATA_DIR / "locations.json"
UNKNOWN_PATH = DATA_DIR / "unknown_locations.json"
CARDS_PATH = DATA_DIR / "cards.json"


def phase(turn: int, max_turn: int) -> str:
    if turn >= max_turn:
        return "final"
    return "early" if turn <= 3 else "mid"


@dataclass
class LocationInfo:
    name: str
    effect: str = ""
    source: str = "unknown"
    bias: dict = field(default_factory=lambda: {"early": 0, "mid": 0, "final": 0})
    tags: list = field(default_factory=list)
    notes: str = ""

    def bias_at(self, turn: int, max_turn: int) -> int:
        return int(self.bias.get(phase(turn, max_turn), 0))

    @property
    def known(self) -> bool:
        return self.source != "unknown"


class LocationKB:
    """data/locations.json 读写（线程安全）；未收录的场地回落到全量资料库的效果文字。"""

    def __init__(self, path: Path = KB_PATH, db=None, use_db: bool = True):
        self.path = path
        self._lock = threading.Lock()
        self._data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self._db = db
        self.use_db = use_db

    def get(self, name: str) -> LocationInfo | None:
        d = self._data.get(name)
        if not isinstance(d, dict):
            return self._from_db(name)
        if not d.get("effect"):
            d = {**d, "effect": self._db_effect(name)}
        return LocationInfo(name=name, effect=d.get("effect", ""), source=d.get("source", "kb"),
                            bias=d.get("bias", {}), tags=d.get("tags", []), notes=d.get("notes", ""))

    def _db_effect(self, name: str) -> str:
        if not self.use_db:
            return ""
        from .knowledge import default_db
        return (self._db or default_db()).location_effect(name)

    def _from_db(self, name: str) -> LocationInfo | None:
        effect = self._db_effect(name)
        return LocationInfo(name=name, effect=effect, source="db") if effect else None

    def put(self, info: LocationInfo) -> None:
        from .kbfile import update_json

        entry = {"effect": info.effect, "source": info.source, "bias": info.bias, "tags": info.tags, "notes": info.notes}
        with self._lock:
            def add(data: dict) -> None:   # 写进磁盘最新版本，不覆盖面板里的人工修改
                data[info.name] = entry

            self._data = update_json(self.path, add)


def load_cards(path: Path = CARDS_PATH) -> dict:
    """卡牌资料：全量资料库 + 人工维护的 cards.json（后者优先）。"""
    from .knowledge import GameDB
    return GameDB(curated_cards=path).cards


class LocationAdvisor:
    """对局中查询场地评估；未知场地交给分析器（可选）后台分析，期间按中性处理。"""

    def __init__(self, kb: LocationKB | None = None, analyst=None, unknown_path: Path = UNKNOWN_PATH):
        self.kb = kb or LocationKB()
        self.analyst = analyst            # 需提供 analyze(name) -> LocationInfo | None
        self.unknown_path = unknown_path
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def info(self, name: str) -> LocationInfo:
        if not name:
            return LocationInfo(name="")
        got = self.kb.get(name)
        if got:
            return got
        self._on_unknown(name)
        return LocationInfo(name=name)

    def _on_unknown(self, name: str) -> None:
        with self._lock:
            if name in self._pending:
                return
            self._pending.add(name)
        self._record_unknown(name)
        if self.analyst is not None:
            threading.Thread(target=self._analyze, args=(name,), daemon=True).start()

    def _analyze(self, name: str) -> None:
        try:
            info = self.analyst.analyze(name)
            if info:
                self.kb.put(info)
                log.info("大模型分析场地 %s：%s bias=%s", name, info.effect, info.bias)
        except Exception as e:  # noqa: BLE001
            log.warning("场地分析失败 %s：%r", name, e)
        finally:
            with self._lock:
                self._pending.discard(name)

    def _record_unknown(self, name: str) -> None:
        try:
            data = json.loads(self.unknown_path.read_text("utf-8")) if self.unknown_path.exists() else {}
            entry = data.setdefault(name, {"first_seen": datetime.now().isoformat(timespec="seconds"), "seen": 0})
            entry["seen"] += 1
            entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
            self.unknown_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
        except OSError as e:
            log.warning("记录未知场地失败：%r", e)

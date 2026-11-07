"""对局记录：每局完整数据存本地，用于赛后复盘与持续改进（后续可迁移到数据库）。

目录结构（logs/games/）：
- <开始时间>_<game_id 前 8 位>.jsonl：一行一个事件
    game_start  {game_id, deck_name, deck}
    turn        {turn, state(GameState), locations(知识库评估), decision{actions, reason, source}, executed[...], outcome,
                 decide_s, llm{location_assessment, target_locations, plan}}
    event       {kind, ...}               新手指引 / 提示处理 / 出牌被拒 等
    game_end    {result, my_locations_won, opp_locations_won, cube_value, final(场地终局), turns_recorded}
- index.jsonl：每局一行摘要（game_id, 时间, 结果, 方块, 文件名），对应数据库里的 games 表

所有对手信息都来自已做可见性过滤的快照（未揭示的牌只有数量）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GAMES_DIR = Path(__file__).resolve().parent.parent / "logs" / "games"


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def judge(final_locations: list[dict]) -> tuple[str, int, int]:
    """按规则判定胜负：赢下更多场地者胜；场地数相同比总战力。-> (win|loss|draw, 我方赢的场地数, 对方赢的场地数)"""
    mine = sum(1 for l in final_locations if l["my_power"] > l["opp_power"])
    theirs = sum(1 for l in final_locations if l["opp_power"] > l["my_power"])
    if mine != theirs:
        return ("win" if mine > theirs else "loss"), mine, theirs
    my_total = sum(l["my_power"] for l in final_locations)
    opp_total = sum(l["opp_power"] for l in final_locations)
    if my_total == opp_total:
        return "draw", mine, theirs
    return ("win" if my_total > opp_total else "loss"), mine, theirs


class GameRecorder:
    def __init__(self, root: Path = GAMES_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.files: dict[str, Path] = {}
        self.started: dict[str, str] = {}
        self.turns: dict[str, int] = {}
        self.ended: set[str] = set()

    def _path(self, game_id: str) -> Path:
        if game_id not in self.files:
            self.started[game_id] = _now()
            self.files[game_id] = self.root / f"{datetime.now():%Y%m%d_%H%M%S}_{game_id[:8]}.jsonl"
        return self.files[game_id]

    def _write(self, game_id: str, rec: dict[str, Any]) -> None:
        rec = {"t": _now(), "game_id": game_id, **rec}
        try:
            with self._path(game_id).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            log.warning("写对局记录失败：%r", e)

    def start(self, game_id: str, deck_name: str = "", deck: list[str] | None = None) -> None:
        if game_id in self.files:
            return
        self._write(game_id, {"type": "game_start", "deck_name": deck_name, "deck": deck or []})

    def turn(self, game_id: str, state: Any, decision: Any, locations: list[dict], executed: list[dict], outcome: str,
             llm: dict | None = None, decide_s: float | None = None) -> None:
        self.turns[game_id] = self.turns.get(game_id, 0) + 1
        self._write(game_id, {
            "type": "turn",
            "turn": state.turn,
            "state": state.model_dump(),
            "locations": locations,
            "decision": decision.model_dump() if hasattr(decision, "model_dump") else decision,
            "executed": executed,
            "outcome": outcome,
            "decide_s": decide_s,
            "llm": llm,      # 大模型原始输出：场地评估（含未收录场地的效果判断）、争夺目标、后续规划
        })

    def event(self, game_id: str, kind: str, **data: Any) -> None:
        self._write(game_id, {"type": "event", "kind": kind, **data})

    def end(self, snap: dict) -> dict | None:
        gid = snap["game_id"]
        if gid in self.ended:
            return None
        self.ended.add(gid)
        final = [{"idx": l["idx"], "name": l.get("id") or l.get("raw_id"), "my_power": l["my_power"], "opp_power": l["opp_power"],
                  "my_cards": [c.get("id") for c in l["my_cards"]], "opp_cards": [c.get("id") for c in l["opp_cards"]]}
                 for l in snap["locations"]]
        result, mine, theirs = judge(final)
        summary = {"result": result, "my_locations_won": mine, "opp_locations_won": theirs,
                   "cube_value": snap.get("cube_value"), "turns_recorded": self.turns.get(gid, 0)}
        self._write(gid, {"type": "game_end", **summary, "final": final})
        try:
            with (self.root / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"game_id": gid, "started": self.started.get(gid), "ended": _now(), **summary,
                                    "locations": [l["name"] for l in final], "file": self._path(gid).name},
                                   ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("写对局索引失败：%r", e)
        return summary

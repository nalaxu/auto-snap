"""竞技场整场记录：logs/arena/runs.jsonl，一行一个事件（追加写），用于赛后统计与规则校准。

事件（都带 t、run_id）：
    run_start  {tickets}
    pick       {round, candidates, pick, scores, reason, source}
    swap       {candidates, out, inc, reason, source}
    game       {game_id, result, cube_value, score_before, score_after, hp_before, hp_after, forfeit}
               forfeit：血量不足抵扣时实际没收的积分（score_before − score_after），用来反推折半的取整规则
    run_end    {score, hp, games, deck}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ARENA_DIR = Path(__file__).resolve().parent.parent / "logs" / "arena"


class ArenaRunLog:
    def __init__(self, root: Path = ARENA_DIR):
        self.root = root
        self.path = root / "runs.jsonl"

    def write(self, kind: str, run_id: str, **data: Any) -> None:
        rec = {"t": datetime.now().isoformat(timespec="seconds"), "kind": kind, "run_id": run_id, **data}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            log.warning("写竞技场记录失败：%r", e)


def read_events(path: Path | None = None) -> list[dict]:
    path = path or ARENA_DIR / "runs.jsonl"
    out = []
    try:
        for line in path.read_text("utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def game_record(before, after, result: str | None, cube_value: int | None, game_id: str = "") -> dict:
    """由一局前后的 ArenaState 生成 game 事件内容。"""
    forfeit = None
    if result == "loss" and after.hp == 0 and cube_value and cube_value > before.hp:
        forfeit = before.score - after.score
    return {"game_id": game_id, "result": result, "cube_value": cube_value,
            "score_before": before.score, "score_after": after.score, "hp_before": before.hp, "hp_after": after.hp,
            "overflow": (cube_value - before.hp) if (result == "loss" and cube_value and cube_value > before.hp) else None,
            "forfeit": forfeit}

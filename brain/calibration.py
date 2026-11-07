"""胜率校准：用历史对局里模型的预估胜率与实际胜负，校正本回合的预估。

样本：logs/games/*.jsonl 里每个回合的 decision.win_probability（模型原始预估），配上该局最终胜负（撤退也算）。
校准：同一阶段（最后一回合 / 其他回合）、预估在 ±WINDOW 以内的历史样本，实际胜率与模型原值按样本量加权：
    p_cal = (胜场 + K·p) / (样本数 + K)
样本少时基本保持原值，样本多了逐渐以历史为准。每局结束后 refresh()。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

GAMES_DIR = Path(__file__).resolve().parent.parent / "logs" / "games"
WINDOW = 0.10
K = 6.0


class Calibrator:
    def __init__(self, root: Path = GAMES_DIR):
        self.root = root
        self._lock = threading.Lock()
        self.samples: list[tuple[float, bool, int]] = []   # (原始预估, 是否最后一回合, 是否获胜)
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for path in self.root.glob("*.jsonl"):
            if path.name == "index.jsonl":
                continue
            turns, result = [], None
            try:
                for line in path.read_text("utf-8").splitlines():
                    r = json.loads(line)
                    if r.get("type") == "turn":
                        d = r.get("decision") or {}
                        p = d.get("win_probability_raw", d.get("win_probability"))
                        if isinstance(p, (int, float)):
                            st = r.get("state") or {}
                            turns.append((float(p), st.get("turn", 0) >= st.get("max_turn", 6)))
                    elif r.get("type") == "game_end":
                        result = r.get("result")
            except (OSError, ValueError):
                continue
            if result in ("win", "loss"):
                rows += [(p, final, 1 if result == "win" else 0) for p, final in turns]
        with self._lock:
            self.samples = rows

    def calibrate(self, p: float | None, final_turn: bool) -> float | None:
        if p is None:
            return None
        with self._lock:
            near = [w for q, f, w in self.samples if f == final_turn and abs(q - p) <= WINDOW]
        return (sum(near) + K * p) / (len(near) + K)

    def summary(self) -> str:
        with self._lock:
            n = len(self.samples)
        return f"胜率校准样本 {n} 个"


_default: Calibrator | None = None


def default_calibrator() -> Calibrator:
    global _default
    if _default is None:
        _default = Calibrator()
    return _default

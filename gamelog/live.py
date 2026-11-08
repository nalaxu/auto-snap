"""实时事件：auto 模式把结构化事件推给外部面板（ui/server），并接收面板的控制命令。

协议（只在 `main.py auto --events` 时启用，平时 emit 什么都不做）：
- 事件：stdout 一行一个，`EVENT_PREFIX + JSON`，JSON 至少有 kind 与 t（unix 秒）。其余 print 输出照常，面板当作原始日志。
- 控制：stdin 一行一个 JSON `{"cmd": "pause" | "resume" | "stop"}`。
    pause  本局打完后不再点 PLAY（停在主界面等 resume）
    resume 取消暂停
    stop   等同按住 F8：下一个检查点抛出 Aborted，进程退出

事件种类（字段见各 payload 函数与 main.auto）：
    session     本次运行的配置（实操/演练、客户端、决策器、模型、最多局数）
    phase       界面阶段：menu / matching / in_game / guide / paused
    game_start  新对局（game_id、卡组）
    turn_state  轮到我方：局面（state_payload）+ 关键信息栈 + 大模型截止时间
    decision    决策结果（decision_payload）
    exec        单个操作的执行结果（snap / retreat / activate / move / play / end_turn）
    turn_done   本回合结束（outcome）
    game_end    对局结束（结果、魔方、对手卡组原型）
    control     收到的控制命令（回显，便于面板确认）
    stopped     进程即将退出（原因）
"""
from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Callable, TextIO

EVENT_PREFIX = "@@AUTOSNAP "


class LiveBus:
    def __init__(self, enabled: bool = False, out: TextIO | None = None):
        self.enabled = enabled
        self.out = out
        self._lock = threading.Lock()
        self.phase: str | None = None

    def emit(self, kind: str, **data: Any) -> None:
        if not self.enabled:
            return
        line = EVENT_PREFIX + json.dumps({"kind": kind, "t": round(time.time(), 3), **data}, ensure_ascii=False, default=str)
        out = self.out or sys.stdout
        with self._lock:
            try:
                out.write(line + "\n")
                out.flush()
            except (OSError, ValueError):
                pass

    def set_phase(self, name: str, **data: Any) -> None:
        """只在阶段变化时发事件。"""
        if name != self.phase:
            self.phase = name
            self.emit("phase", name=name, **data)


def parse_event(line: str) -> dict | None:
    """面板侧：stdout 的一行 -> 事件 dict；不是事件返回 None。"""
    line = line.rstrip("\r\n")
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        ev = json.loads(line[len(EVENT_PREFIX):])
    except json.JSONDecodeError:
        return None
    return ev if isinstance(ev, dict) and "kind" in ev else None


class Control:
    """面板控制命令的状态。paused / stop 由 stdin 读线程设置，主循环读取。"""

    COMMANDS = ("pause", "resume", "stop")

    def __init__(self, on_stop: Callable[[], None] | None = None, bus: LiveBus | None = None):
        self.paused = False
        self.stop_requested = False
        self.on_stop = on_stop
        self.bus = bus

    def apply(self, cmd: str) -> bool:
        if cmd not in self.COMMANDS:
            return False
        if cmd == "pause":
            self.paused = True
        elif cmd == "resume":
            self.paused = False
        elif cmd == "stop":
            self.stop_requested = True
            if self.on_stop:
                self.on_stop()
        if self.bus:
            self.bus.emit("control", cmd=cmd, paused=self.paused)
        return True

    def handle_line(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return False
        try:
            msg = json.loads(line)
            cmd = msg.get("cmd") if isinstance(msg, dict) else None
        except json.JSONDecodeError:
            cmd = line
        return self.apply(str(cmd or ""))

    def start_stdin_reader(self, stream: TextIO | None = None) -> threading.Thread:
        stream = stream or sys.stdin

        def run() -> None:
            try:
                for line in stream:
                    self.handle_line(line)
            except (OSError, ValueError):
                pass

        t = threading.Thread(target=run, name="live-control", daemon=True)
        t.start()
        return t


# ---------------------------------------------------------------- payload：GameState / TurnDecision -> 面板用的精简 dict
# 输入都是 model_dump() 后的 dict，这样实时运行与对局记录回放（ui/replay.py）共用同一套转换。

def _card_name(db, cid: str) -> str:
    if db is None or not cid:
        return cid or ""
    return (db.card(cid) or {}).get("name") or cid


def _card(c: dict, db) -> dict:
    out = {"id": c.get("id"), "name": c.get("name") or _card_name(db, c.get("id", "")), "cost": c.get("cost"),
           "power": c.get("power")}
    for k in ("turn_played", "activated", "screen_activatable"):
        if c.get(k):
            out[k] = c[k]
    if db is not None and c.get("id"):
        info = db.card(c["id"]) or {}
        if info.get("ability"):
            out["ability"] = info["ability"]
    return out


def _loc_name(db, lid: str) -> str:
    if db is None or not lid:
        return lid or ""
    return (db.location(lid) or {}).get("name") or lid


def state_payload(state: dict, location_report: list[dict] | None = None, db=None) -> dict:
    report = {r.get("idx"): r for r in (location_report or [])}
    locs = []
    for l in state.get("locations", []):
        rep = report.get(l.get("idx"), {})
        lid = l.get("name") or ""
        opp_cards = [_card(c, db) for c in l.get("opp_cards", [])]
        locs.append({
            "idx": l.get("idx"),
            "id": lid,
            "name": _loc_name(db, lid),
            "effect": rep.get("effect") or l.get("effect") or (db.location_effect(lid) if db is not None and lid else ""),
            "revealed": l.get("revealed", True),
            "my_power": l.get("my_power", 0),
            "opp_power": l.get("opp_power", 0),
            "my_cards": [_card(c, db) for c in l.get("my_cards", [])],
            "opp_cards": opp_cards,
            "opp_hidden": max(0, (l.get("opp_card_count") or 0) - len(opp_cards)),
            "playable": l.get("playable", True),
            "bias": rep.get("bias"),
        })
    return {
        "turn": state.get("turn"),
        "max_turn": state.get("max_turn", 6),
        "energy": state.get("energy"),
        "cube_stake": state.get("cube_stake"),
        "cube_next": state.get("cube_next"),
        "can_snap": state.get("can_snap"),
        "opp_snapped": state.get("opp_snapped"),
        "turn_timer": state.get("turn_timer"),
        "timer_left": state.get("turn_seconds_left"),
        "timer_total": state.get("turn_seconds_total"),
        "hand": [_card(c, db) for c in state.get("hand", [])],
        "deck_count": state.get("my_deck_count") if state.get("my_deck_count") is not None else len(state.get("deck_remaining") or []),
        "graveyard": len(state.get("my_graveyard") or []),
        "opp_hand": state.get("opp_hand_count"),
        "opp_deck": state.get("opp_deck_count"),
        "locations": locs,
        "notes": list(state.get("board_notes") or []),
        "forbidden": [list(x) for x in state.get("forbidden") or []],
    }


def decision_payload(decision: dict, state: dict | None = None, llm_raw: dict | None = None, decide_s: float | None = None,
                     note: str = "", retreat_why: str = "", invalid: list[str] | None = None, db=None) -> dict:
    loc_names = {l.get("idx"): _loc_name(db, l.get("name") or "") for l in (state or {}).get("locations", [])}

    def where(i):
        return {"location": i, "location_name": loc_names.get(i) or f"地点{i}"}

    source = decision.get("source") or ""
    reason = decision.get("reason") or ""
    return {
        "source": source,
        "fallback": source.startswith("rule") or "规则AI" in reason,
        "skipped": source.startswith("skip"),
        "decide_s": decide_s,
        "actions": [{"card": a["card"], "name": _card_name(db, a["card"]), **where(a["location"])} for a in decision.get("actions", [])],
        "activate": [{"card": a["card"], "name": _card_name(db, a["card"]), **where(a["location"])} for a in decision.get("activate", [])],
        "moves": [{"card": m["card"], "name": _card_name(db, m["card"]), "from": m["from_location"], "to": m["to_location"],
                   "from_name": loc_names.get(m["from_location"]), "to_name": loc_names.get(m["to_location"])}
                  for m in decision.get("moves", [])],
        "snap": bool(decision.get("snap")),
        "retreat": bool(decision.get("retreat")),
        "win_prob": decision.get("win_probability"),
        "win_prob_raw": decision.get("win_probability_raw"),
        "retreat_why": retreat_why,
        "reason": reason,
        "plan": (llm_raw or {}).get("plan") or "",
        "note": note,
        "invalid": invalid or [],
    }

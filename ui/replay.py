"""回放演示：把一局对局记录（logs/games/*.jsonl）按实时事件协议重新播放，不连游戏、不调模型。

用途：没开游戏时调试面板、给别人演示。面板「回放演示」会以子进程启动它，协议与 `main.py auto --events` 相同。

    python -m ui.replay logs/games/20261107_225328_773d5c5d.jsonl --speed 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from gamelog.live import Control, LiveBus, decision_payload, state_payload


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class Stopped(Exception):
    pass


def play(records: list[dict], bus: LiveBus, control: Control, speed: float = 1.0, db=None, games_done: int = 0) -> dict | None:
    def wait(seconds: float) -> None:
        end = time.time() + seconds / max(speed, 0.05)
        while time.time() < end or control.paused:
            if control.stop_requested:
                raise Stopped
            time.sleep(0.05)

    start = next((r for r in records if r.get("type") == "game_start"), {})
    gid = (records[0] if records else {}).get("game_id", "demo")
    bus.set_phase("matching")
    print("  ▶ 回放：匹配中…")
    wait(1.0)
    bus.set_phase("in_game")
    bus.emit("game_start", game_id=gid, deck_name=start.get("deck_name", ""), games_done=games_done)
    for r in records:
        if r.get("type") != "turn":
            continue
        st = r.get("state") or {}
        dec = r.get("decision") or {}
        decide_s = float(r.get("decide_s") or 0)
        deadline = 0.0 if str(dec.get("source", "")).startswith("skip") else (35.0 if st.get("turn", 1) >= 5 else 25.0)
        print(f"T{st.get('turn')} 能量{st.get('energy')} 赌注{st.get('cube_stake')}（回放）")
        bus.emit("turn_state", game_id=gid, turn=st.get("turn"), state=state_payload(st, r.get("locations"), db), deadline=deadline)
        wait(min(decide_s, 8.0) if decide_s else 0.6)
        bus.emit("decision", game_id=gid, turn=st.get("turn"),
                 decision=decision_payload(dec, st, r.get("llm"), decide_s, db=db))
        if dec.get("reason"):
            print(f"    理由：{dec['reason']}")
        for item in r.get("executed") or []:
            wait(0.9)
            bus.emit("exec", game_id=gid, turn=st.get("turn"), **item)
        if r.get("outcome") == "done":
            wait(0.4)
            bus.emit("exec", game_id=gid, turn=st.get("turn"), end_turn=True, ok=True)
        bus.emit("turn_done", game_id=gid, turn=st.get("turn"), outcome=r.get("outcome"))
        wait(2.5)
    end = next((r for r in records if r.get("type") == "game_end"), None)
    if end:
        bus.emit("game_end", game_id=gid, games_done=games_done + 1, archetype=None,
                 **{k: end.get(k) for k in ("result", "retreat", "my_locations_won", "opp_locations_won", "cube_value",
                                             "turns_recorded")})
        print(f"对局 {gid[:8]} 结束：{end.get('result')}（回放）")
    bus.set_phase("menu")
    return end


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", action="store_true", help="放完一局再从头放")
    args = ap.parse_args()

    from brain.knowledge import default_db

    bus = LiveBus(enabled=True)
    control = Control(bus=bus)
    control.start_stdin_reader()
    records = load(Path(args.file))
    start = next((r for r in records if r.get("type") == "game_start"), {})
    bus.emit("session", live=False, demo=True, client=start.get("client") or "replay", client_label="回放演示",
             decider="replay", model=Path(args.file).name, effort=None, max_games=0, max_turns=0, snap=True, retreat=True)
    reason, done = "回放结束", 0
    try:
        while True:
            play(records, bus, control, args.speed, default_db(), done)
            done += 1
            if not args.loop:
                break
            time.sleep(2 / max(args.speed, 0.05))
    except Stopped:
        reason = "面板停止"
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    bus.emit("stopped", reason=reason, games_done=done)


if __name__ == "__main__":
    main()

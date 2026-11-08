"""外部面板：事件协议（gamelog/live.py）、机器人子进程参数（ui/server/bot.py）、后端接口（ui/server/app.py）。"""
from __future__ import annotations

import io
import json
import time

import pytest

from gamelog.live import EVENT_PREFIX, Control, LiveBus, decision_payload, parse_event, state_payload


class FakeDB:
    def card(self, cid):
        return {"SpiderMan": {"name": "Spider-Man", "power": 4, "ability": "On Reveal: Move."}}.get(cid, {})

    def location(self, lid):
        return {"KunLun": {"name": "K'un-Lun", "effect": "When a card moves here, give it +2 Power."}}.get(lid)

    def location_effect(self, lid):
        return (self.location(lid) or {}).get("effect", "")


STATE = {
    "turn": 3, "max_turn": 6, "energy": 3, "cube_stake": 2, "cube_next": 4, "turn_timer": True,
    "turn_seconds_left": 40.0, "turn_seconds_total": 60.0,
    "hand": [{"id": "SpiderMan", "name": "", "cost": 2, "power": 4}],
    "deck_remaining": ["A", "B"], "my_graveyard": [], "opp_hand_count": 4, "opp_deck_count": 7,
    "locations": [
        {"idx": 0, "name": "KunLun", "revealed": True, "my_power": 3, "opp_power": 5, "my_cards": [],
         "opp_card_count": 2, "opp_cards": [{"id": "Wolverine", "name": "", "cost": 2, "power": 3}]},
        {"idx": 1, "name": "", "revealed": False, "my_power": 0, "opp_power": 0, "my_cards": [], "opp_card_count": 0, "opp_cards": []},
        {"idx": 2, "name": "Vault", "revealed": True, "my_power": 0, "opp_power": 0, "my_cards": [], "opp_card_count": 0, "opp_cards": []},
    ],
    "board_notes": ["【本回合有效】KunLun +2"],
    "forbidden": [("SpiderMan", 2)],
}


def test_bus_disabled_emits_nothing():
    out = io.StringIO()
    LiveBus(enabled=False, out=out).emit("phase", name="menu")
    assert out.getvalue() == ""


def test_bus_roundtrip_and_phase_dedup():
    out = io.StringIO()
    bus = LiveBus(enabled=True, out=out)
    bus.set_phase("menu")
    bus.set_phase("menu")
    bus.emit("exec", card="中文卡", ok=True)
    lines = out.getvalue().splitlines()
    assert len(lines) == 2 and all(l.startswith(EVENT_PREFIX) for l in lines)
    ev = parse_event(lines[1])
    assert ev["kind"] == "exec" and ev["card"] == "中文卡" and ev["t"] > 0
    assert parse_event("普通日志") is None
    assert parse_event(EVENT_PREFIX + "{坏的") is None


def test_control_commands():
    stopped = []
    out = io.StringIO()
    c = Control(on_stop=lambda: stopped.append(1), bus=LiveBus(enabled=True, out=out))
    assert c.handle_line('{"cmd": "pause"}') and c.paused
    assert c.handle_line("resume") and not c.paused
    assert not c.handle_line('{"cmd": "rm -rf"}')
    assert c.handle_line('{"cmd": "stop"}') and c.stop_requested and stopped == [1]
    kinds = [parse_event(l)["cmd"] for l in out.getvalue().splitlines()]
    assert kinds == ["pause", "resume", "stop"]


def test_control_stdin_reader():
    c = Control()
    t = c.start_stdin_reader(io.StringIO('{"cmd":"pause"}\n'))
    t.join(1)
    assert c.paused


def test_state_payload():
    p = state_payload(STATE, [{"idx": 2, "effect": "On turn 6, cards can't be played here.", "bias": -1}], FakeDB())
    assert p["turn"] == 3 and p["timer_left"] == 40.0 and p["deck_count"] == 2 and p["opp_hand"] == 4
    assert p["hand"][0]["name"] == "Spider-Man" and p["hand"][0]["ability"] == "On Reveal: Move."
    k = p["locations"][0]
    assert k["name"] == "K'un-Lun" and k["effect"].startswith("When a card moves") and k["opp_hidden"] == 1
    assert p["locations"][1]["revealed"] is False
    assert p["locations"][2]["effect"].startswith("On turn 6") and p["locations"][2]["bias"] == -1
    assert p["notes"] == ["【本回合有效】KunLun +2"] and p["forbidden"] == [["SpiderMan", 2]]
    json.dumps(p)  # 可序列化


def test_decision_payload():
    d = {"actions": [{"type": "play", "card": "SpiderMan", "location": 0}], "activate": [],
         "moves": [{"card": "SpiderMan", "from_location": 0, "to_location": 2}], "snap": True, "retreat": False,
         "win_probability": 0.55, "win_probability_raw": 0.68, "reason": "[大模型 25s 内未返回，规则AI兜底] x",
         "source": "rule_based"}
    p = decision_payload(d, STATE, {"plan": "守住左路"}, 3.2, db=FakeDB())
    assert p["fallback"] and not p["skipped"] and p["snap"]
    assert p["actions"][0] == {"card": "SpiderMan", "name": "Spider-Man", "location": 0, "location_name": "K'un-Lun"}
    assert p["moves"][0]["to_name"] == "Vault" and p["plan"] == "守住左路" and p["win_prob_raw"] == 0.68
    skip = decision_payload({"source": "skip:no_playable", "reason": "没牌"}, STATE)
    assert skip["skipped"] and not skip["fallback"]


def test_build_args():
    from ui.server.bot import BotProcess

    a = BotProcess.build_args({"live": True, "client": "cn_official", "decider": "rule_based", "max_games": 3,
                               "skip_current": True})
    assert a[2:] == [a[2], "auto", "--events", "--live", "--client", "cn_official", "--decider", "rule_based",
                     "--max-games", "3", "--skip-current"]
    assert "--live" not in BotProcess.build_args({"live": False, "decider": "rm"})
    assert "--decider" not in BotProcess.build_args({"decider": "rm"})
    demo = BotProcess.build_args({"demo": "x.jsonl", "speed": 2})
    assert demo[2:4] == ["-m", "ui.replay"] and "--speed" in demo


def test_bot_process_reads_events(tmp_path):
    """真实子进程：回放一局极短的对局记录，事件与原始日志都能收到，进程退出后有 exited。"""
    from ui.server.bot import BotProcess

    game = tmp_path / "g.jsonl"
    rows = [
        {"type": "game_start", "game_id": "abcd1234", "deck_name": "loki"},
        {"type": "turn", "game_id": "abcd1234", "turn": 1, "state": STATE, "locations": [],
         "decision": {"actions": [], "source": "skip:no_playable"}, "executed": [], "outcome": "done", "decide_s": 0},
        {"type": "game_end", "game_id": "abcd1234", "result": "win", "cube_value": 2},
    ]
    game.write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    events: list[dict] = []
    bot = BotProcess(events.append)
    bot.start({"demo": str(game), "speed": 50})
    end = time.time() + 30
    while time.time() < end and not any(e.get("state") == "exited" for e in events):
        time.sleep(0.1)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "process" and kinds[-1] == "process"
    for k in ("session", "game_start", "turn_state", "decision", "game_end", "stopped", "log"):
        assert k in kinds, k
    assert events[-1]["code"] == 0 and not bot.running


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from ui.server.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_api_basics(client):
    st = client.get("/api/status").json()
    assert st["running"] is False
    opts = client.get("/api/options").json()
    assert {c["key"] for c in opts["clients"]} >= {"cn_official", "cn_steam"}
    assert client.post("/api/control", json={"cmd": "stop"}).status_code == 409
    assert client.post("/api/control", json={"cmd": "explode"}).status_code == 400
    assert client.post("/api/start", json={"demo": "../../config/settings.toml"}).status_code == 404
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["kind"] == "hello" and hello["status"]["running"] is False

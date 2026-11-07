"""对局记录：胜负判定与落盘格式。"""
import json

from brain.models import Card, GameState, Location, PlayAction, TurnDecision
from gamelog.recorder import GameRecorder, judge


def test_judge_by_locations_then_total_power():
    L = lambda m, o: {"my_power": m, "opp_power": o}
    assert judge([L(9, 8), L(20, 10), L(8, 25)]) == ("win", 2, 1)     # 实机 d5da1d36
    assert judge([L(1, 5), L(3, 4), L(9, 2)]) == ("loss", 1, 2)
    assert judge([L(5, 5), L(10, 1), L(1, 3)]) == ("win", 1, 1)       # 场地 1:1，总战力 16 > 9
    assert judge([L(5, 5), L(5, 5), L(5, 5)])[0] == "draw"


def test_recorder_writes_turns_end_and_index(tmp_path):
    rec = GameRecorder(tmp_path)
    rec.start("g123456789", "入门卡组", ["Hulk"])
    st = GameState(turn=1, energy=1, hand=[Card(id="Quicksilver", cost=1, power=2)],
                   locations=[Location(idx=i, name="Ruins") for i in range(3)])
    d = TurnDecision(actions=[PlayAction(card="Quicksilver", location=0)], reason="test", source="rule_based")
    rec.turn("g123456789", st, d, [{"idx": 0, "bias": 0}], [{"card": "Quicksilver", "location": 0, "ok": True}], "done")
    rec.event("g123456789", "overlay", name="never_seen")
    snap = {"game_id": "g123456789", "cube_value": 2, "locations": [
        {"idx": i, "id": "Ruins", "my_power": m, "opp_power": o, "my_cards": [], "opp_cards": []}
        for i, (m, o) in enumerate([(9, 8), (20, 10), (8, 25)])]}
    assert rec.end(snap)["result"] == "win"
    assert rec.end(snap) is None  # 只记录一次
    lines = [json.loads(x) for x in next(tmp_path.glob("*_g1234567.jsonl")).read_text("utf-8").splitlines()]
    assert [x["type"] for x in lines] == ["game_start", "turn", "event", "game_end"]
    assert lines[1]["decision"]["actions"][0]["card"] == "Quicksilver"
    idx = [json.loads(x) for x in (tmp_path / "index.jsonl").read_text("utf-8").splitlines()]
    assert idx[0]["result"] == "win" and idx[0]["cube_value"] == 2


def test_retreat_overrides_location_judgement(tmp_path):
    from gamelog.recorder import GameRecorder

    rec = GameRecorder(tmp_path)
    locs = [{"idx": i, "id": "X", "my_power": 0, "opp_power": 0, "my_cards": [], "opp_cards": []} for i in range(3)]
    base = {"cube_value": 1, "locations": locs}
    assert rec.end({**base, "game_id": "a" * 36, "me": {}, "opp": {"conceded": True}})["result"] == "win"
    s = rec.end({**base, "game_id": "b" * 36, "me": {"conceded": True}, "opp": {}})
    assert s["result"] == "loss" and s["retreat"] == "me"

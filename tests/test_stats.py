"""战绩 / 复盘汇总（gamelog/stats.py）与面板接口 /api/stats、/api/games。"""
from __future__ import annotations

import json

import pytest

from gamelog import stats


def _state(turn, energy, hand, locs=("KunLun", "Vault", "")):
    return {"turn": turn, "max_turn": 6, "energy": energy, "cube_stake": 1, "hand": hand,
            "locations": [{"idx": i, "name": n, "revealed": bool(n), "my_power": 0, "opp_power": 0, "my_cards": [],
                           "opp_card_count": 0, "opp_cards": []} for i, n in enumerate(locs)]}


def _write_game(root, file, gid, result, cube, turns, client="cn_official", retreat=None):
    rows = [{"type": "game_start", "game_id": gid, "client": client, "deck_name": "loki"}]
    for t in turns:
        rows.append({"type": "turn", "game_id": gid, **t})
    final = [{"idx": 0, "name": "KunLun", "my_power": 5, "opp_power": 3, "my_cards": ["SpiderMan"], "opp_cards": []},
             {"idx": 1, "name": "Vault", "my_power": 1, "opp_power": 4, "my_cards": [], "opp_cards": ["Wong"]},
             {"idx": 2, "name": "Limbo", "my_power": 2, "opp_power": 2, "my_cards": [], "opp_cards": []}]
    rows.append({"type": "event", "game_id": gid, "kind": "overlay", "name": "never_seen"})
    rows.append({"type": "game_end", "game_id": gid, "result": result, "retreat": retreat, "cube_value": cube, "final": final})
    (root / file).write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    with (root / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"game_id": gid, "started": "2026-11-08T10:00:00.000", "result": result, "retreat": retreat,
                            "cube_value": cube, "turns_recorded": len(turns), "locations": ["KunLun", "Vault", "Limbo"],
                            "file": file}) + "\n")


HAND = [{"id": "SpiderMan", "cost": 2, "power": 4}, {"id": "Wong", "cost": 4, "power": 2}]


@pytest.fixture()
def games_dir(tmp_path):
    t1 = {"turn": 1, "state": _state(1, 2, HAND), "locations": [], "outcome": "done", "decide_s": 8.0,
          "decision": {"actions": [{"card": "SpiderMan", "location": 0}], "source": "llm:gpt/low", "win_probability": 0.6},
          "executed": [{"card": "SpiderMan", "location": 0, "ok": True}],
          "llm": {"location_assessment": [{"location": 1, "favorable_now": -2, "why": "第 6 回合封锁"}], "target_locations": [0]}}
    t2 = {"turn": 2, "state": _state(2, 6, HAND), "locations": [], "outcome": "done", "decide_s": 30.0,
          "decision": {"actions": [], "source": "rule_based", "snap": True, "reason": "[大模型 25s 内未返回，规则AI兜底]"},
          "executed": [{"snap": True, "ok": True}, {"card": "Wong", "location": 1, "ok": False, "error": "rejected"}]}
    _write_game(tmp_path, "a.jsonl", "aaaa", "win", 4, [t1, t2])
    _write_game(tmp_path, "b.jsonl", "bbbb", "loss", 2, [t1], client="cn_steam")
    _write_game(tmp_path, "c.jsonl", "cccc", "win", 1, [], retreat="opp")
    return tmp_path


def test_turn_flags():
    t = {"state": _state(2, 6, HAND), "outcome": "done", "decision": {"source": "rule_based"},
         "executed": [{"snap": True, "ok": True}, {"card": "Wong", "location": 1, "ok": False, "error": "rejected"}]}
    assert set(stats.turn_flags(t)) == {"fallback", "rejected", "snap", "waste"}
    played_all = {"state": _state(1, 2, HAND), "outcome": "done", "decision": {"source": "llm"},
                  "executed": [{"card": "SpiderMan", "location": 0, "ok": True}]}
    assert stats.turn_flags(played_all) == []   # 剩 0 能量：不算浪费


def test_list_and_aggregate(games_dir):
    games = stats.list_games(games_dir)
    assert [g["result"] for g in games] == ["win", "loss", "win"]
    a = games[0]
    assert a["cubes"] == 4 and a["client"] == "cn_official" and a["snap_turn"] == 2 and a["fallbacks"] == 1
    assert a["rejected"] == 1 and a["decide_median"] == 8.0 and a["locations"][0]["id"] == "KunLun"
    agg = stats.aggregate(games, games_dir)
    assert (agg["games"], agg["wins"], agg["losses"], agg["net_cubes"]) == (3, 2, 1, 3)
    assert [p["total"] for p in agg["trend"]] == [4, 2, 3]
    assert agg["streak"]["best_win"] == 1 and agg["streak"]["current_result"] == "win"
    assert agg["retreat"] == {"me": 0, "opp": 1} and agg["snap"] == {"games": 1, "wins": 1, "cubes": 4}
    assert {r["cube"]: (r["win"], r["loss"]) for r in agg["cube_dist"]} == {4: (1, 0), 2: (0, 1), 1: (1, 0)}
    d = agg["decisions"]
    assert d["total"] == 3 and d["llm"] == 2 and d["fallback"] == 1 and d["median_s"] == 8.0
    kun = next(l for l in agg["locations"] if l["id"] == "KunLun")
    assert kun["games"] == 3 and kun["won"] == 3
    assert {g["key"] for g in agg["by_client"]} == {"cn_official", "cn_steam"}


def test_game_detail(games_dir):
    d = stats.game_detail("a.jsonl", games_dir)
    assert d["summary"]["result"] == "win" and len(d["turns"]) == 2
    t1, t2 = d["turns"]
    assert t1["decision"]["actions"][0]["card"] == "SpiderMan" and t1["targets"] == [0]
    assert t1["assessment"][0]["location"] == 1 and t1["assessment"][0]["why"] == "第 6 回合封锁"
    assert "fallback" in t2["flags"] and t2["decision"]["fallback"]
    assert d["final"][0]["my_power"] == 5 and d["final"][1]["opp_cards"][0]["id"] == "Wong"
    assert d["trailing_events"][0]["kind"] == "overlay"
    assert stats.game_detail("../index.jsonl", games_dir) is None
    assert stats.game_detail("missing.jsonl", games_dir) is None


def test_empty_dir(tmp_path):
    assert stats.list_games(tmp_path) == []
    agg = stats.aggregate([], tmp_path)
    assert agg["games"] == 0 and agg["win_rate"] is None and agg["trend"] == []


def test_api(games_dir, monkeypatch):
    from fastapi.testclient import TestClient

    from ui.server import app as app_mod

    monkeypatch.setattr(app_mod, "GAMES_DIR", games_dir)
    with TestClient(app_mod.create_app()) as c:
        s = c.get("/api/stats").json()
        assert s["games"] == 3 and set(s["clients"]) == {"cn_official", "cn_steam"}
        assert c.get("/api/stats", params={"client": "cn_steam"}).json()["games"] == 1
        assert c.get("/api/stats", params={"last": 2}).json()["games"] == 2
        g = c.get("/api/games").json()
        assert [x["file"] for x in g] == ["c.jsonl", "b.jsonl", "a.jsonl"]   # 新的在前
        assert c.get("/api/games/a.jsonl").json()["summary"]["cube_value"] == 4
        assert c.get("/api/games/nope.jsonl").status_code == 404

"""竞技场 M4 / M5：整场记录、竞技场战绩、面板启动参数与接口。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from executor.arena_flow import ArenaFlow, ArenaOptions, state_payload
from gamelog import arena_stats
from gamelog.arena_log import ArenaRunLog, game_record, read_events
from perception.arena import ArenaState
from ui.server.bot import BotProcess

DECK = ["Kingpin", "MariaHill", "Scorpion", "EzekielSims", "JeffTheBabyDolphin", "AuntMay", "Shanna", "Brood",
        "DraxAvatarOfLife", "Wiccan", "SpiderMan2099", "Magneto"]


def st(**kw) -> ArenaState:
    base = dict(run_id="run1", finished=False, deck=list(DECK), candidates=["Leader", "Magneto", "Shocker"], score=4, hp=6,
                games=2, version=10)
    base.update(kw)
    return ArenaState(**base)


def test_game_record_forfeit():
    rec = game_record(st(score=12, hp=1), st(score=5, hp=0, games=3), "loss", 8)
    assert rec["overflow"] == 7 and rec["forfeit"] == 7
    rec = game_record(st(score=4, hp=10), st(score=4, hp=6, games=3), "loss", 4)
    assert rec["forfeit"] is None and rec["overflow"] is None


def test_flow_observe_logs_game_and_emits_state(tmp_path):
    events = []
    log = ArenaRunLog(tmp_path)
    f = ArenaFlow(ex=None, read_arena=lambda: None, options=ArenaOptions(), emit=lambda k, **d: events.append((k, d)),
                  live=False, run_log=log)
    f.observe(st())
    f.note_game("g1", "win", 4)
    f.observe(st(score=8, games=3, version=11))
    f.observe(st(score=8, games=3, version=11, finished=True))
    recs = read_events(log.path)
    kinds = [r["kind"] for r in recs]
    assert kinds == ["game", "run_end"]
    g = recs[0]
    assert (g["game_id"], g["score_before"], g["score_after"], g["hp_after"]) == ("g1", 4, 8, 6)
    states = [d for k, d in events if k == "arena_state"]
    assert states and states[-1]["score"] == 8 and states[-1]["deck"][0]["id"] == "Kingpin"
    assert "name" in states[-1]["deck"][0] and states[-1]["next_tier"] == 10


def test_state_payload_tiers():
    assert state_payload(st(score=13))["next_tier"] == 15
    assert state_payload(st(score=35))["next_tier"] is None


def test_arena_stats_aggregate():
    events = [
        {"kind": "run_start", "run_id": "a", "t": "1"},
        {"kind": "pick", "run_id": "a", "pick": "Kingpin"},
        {"kind": "pick", "run_id": "a", "pick": "Isca"},
        {"kind": "swap", "run_id": "a", "out": "Isca", "inc": "Magneto"},
        {"kind": "swap", "run_id": "a", "out": None},
        {"kind": "game", "run_id": "a", "result": "win", "score_after": 4, "hp_after": 10},
        {"kind": "game", "run_id": "a", "result": "loss", "score_after": 2, "hp_after": 0, "forfeit": 2, "overflow": 3,
         "hp_before": 1, "cube_value": 4},
        {"kind": "run_end", "run_id": "a", "score": 2, "hp": 0, "games": 2},
        {"kind": "run_start", "run_id": "b", "t": "2"},
    ]
    games = [{"result": "win", "cubes": 4, "arena_hp": 10, "snap_turn": 3, "retreat": None},
             {"result": "loss", "cubes": -4, "arena_hp": 1, "snap_turn": None, "retreat": "me"}]
    a = arena_stats.aggregate(events, games)
    assert a["runs_total"] == 2 and a["runs_finished"] == 1 and a["avg_score"] == 2
    run_a = next(r for r in a["runs"] if r["run_id"] == "a")
    assert run_a["deck"] == ["Kingpin", "Magneto"] and run_a["tier"] == 0 and run_a["declined_swaps"] == 1
    assert a["forfeits"][0]["forfeit"] == 2
    hp = {r["hp"]: r for r in a["by_hp"]}
    assert hp["7–10"]["snaps"] == 1 and hp["1–3"]["retreats"] == 1


def test_bot_args_arena():
    args = BotProcess.build_args({"game_mode": "arena", "max_runs": 2, "buy_tickets": False, "live": True})
    assert args[args.index("--game-mode") + 1] == "arena"
    assert args[args.index("--max-runs") + 1] == "2" and "--no-buy-tickets" in args
    args = BotProcess.build_args({"game_mode": "ranked", "max_runs": 2, "buy_tickets": True})
    assert "--max-runs" not in args and "--buy-tickets" not in args


@pytest.fixture()
def ratings_client(tmp_path):
    from fastapi import FastAPI

    from ui.server.arena import create_router

    path = tmp_path / "ratings.json"
    path.write_text(json.dumps({"items": {"Leader": {"rating": 8.5, "tags": ["standalone"], "note": "x", "source": "auto"}}}), "utf-8")
    app = FastAPI()
    app.include_router(create_router(path))
    return TestClient(app), path


def test_ratings_api_list_and_put(ratings_client):
    c, path = ratings_client
    d = c.get("/api/arena/ratings", params={"q": "leader"}).json()
    assert d["rows"][0]["id"] == "Leader" and d["rows"][0]["rating"] == 8.5
    assert c.put("/api/arena/ratings/Leader", json={"rating": 7.3, "tags": ["tech"], "note": "人工"}).json()["ok"]
    e = json.loads(path.read_text("utf-8"))["items"]["Leader"]
    assert e == {"rating": 7.5, "tags": ["tech"], "note": "人工", "source": "manual"}
    assert c.put("/api/arena/ratings/Leader", json={"rating": 5, "tags": ["bogus"], "note": ""}).status_code == 400
    assert c.get("/api/arena/ratings", params={"source": "manual"}).json()["matched"] == 1

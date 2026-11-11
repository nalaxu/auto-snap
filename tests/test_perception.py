"""感知层：快照 -> GameState、剩余牌库推算、出牌阶段判断、卡组读取（不需要游戏进程）。"""
import json
from types import SimpleNamespace as NS

import pytest

from brain.validator import validate
from brain import rule_based
from perception.deck import load_current_deck
from perception.memory.snap import remaining_deck, to_game_state
from perception.memory_source import TurnGate, is_my_turn

DECK = ["Quicksilver", "Hawkeye", "Medusa", "Cyclops", "Starlord", "TheThing",
        "IronMan", "Hulk", "Sentinel", "MistyKnight", "Gamora", "Nightcrawler"]


def card(cid, cost, power, owner="me", entity=1, **kw):
    return {"entity": entity, "owner": owner, "zone": "Hand", "revealed": True, "id": cid, "cost": cost, "power": power, **kw}


def snap(**over):
    s = {
        "game_id": "g1", "turn": 3, "total_turns": 6, "cube_value": 1, "finished": False,
        "me": {"entity": 1, "energy": 3, "max_energy": 3, "ended_turn": 2, "deck_count": 6,
               "hand": [card("Quicksilver", 1, 2), card("Medusa", 2, 2), card("Cyclops", 3, 5)],
               "graveyard": [], "banished": []},
        "opp": {"entity": 6, "hand_count": 3, "deck_count": 7},
        "locations": [
            {"idx": 0, "id": "LosDiablos", "revealed": True, "my_power": 6, "opp_power": 0,
             "my_cards": [card("Starlord", 2, 2), card("Hawkeye", 1, 4)], "opp_cards": []},
            {"idx": 1, "id": "Wakanda", "revealed": True, "my_power": 0, "opp_power": 2, "my_cards": [],
             "opp_cards": [card("Nightcrawler", 1, 2, owner="opp")]},
            {"idx": 2, "id": None, "revealed": False, "my_power": 0, "opp_power": 4, "my_cards": [],
             "opp_cards": [{"entity": 40, "owner": "opp", "zone": "Location", "revealed": False}]},
        ],
    }
    s.update(over)
    return s


def test_to_game_state_and_hidden_cards():
    gs = to_game_state(snap(), DECK)
    assert [c.id for c in gs.hand] == ["Quicksilver", "Medusa", "Cyclops"]
    assert [c.hand_index for c in gs.hand] == [0, 1, 2]
    assert gs.locations[1].opp_cards[0].id == "Nightcrawler"
    assert gs.locations[2].opp_card_count == 1 and gs.locations[2].opp_cards == []  # 未揭示只计数
    assert gs.locations[2].name == "" and not gs.locations[2].revealed
    assert validate(gs, rule_based.decide(gs)) == []


def test_remaining_deck_excludes_seen_cards():
    left = remaining_deck(DECK, snap())
    assert sorted(left) == sorted(["TheThing", "IronMan", "Hulk", "Sentinel", "MistyKnight", "Gamora", "Nightcrawler"])
    # 对手的 Nightcrawler 不影响我方牌库


def test_is_my_turn():
    assert is_my_turn(snap())
    assert not is_my_turn(snap(finished=True))
    assert not is_my_turn(snap(turn=0))
    s = snap()
    s["me"]["ended_turn"] = 3
    assert not is_my_turn(s)


def test_load_current_deck(tmp_path):
    (tmp_path / "PlayState.json").write_text(json.dumps({"SelectedDeckId": {"Value": "d2"}}), "utf-8")
    coll = {"ServerState": {"Decks": [
        {"$id": "1", "Id": "d1", "Name": "A", "Cards": [{"CardDefId": "Hulk"}]},
        {"$id": "2", "Id": "d2", "Name": "B", "Cards": [{"$id": "3", "CardDefId": "Medusa"}, {"$ref": "3"}]},
    ]}}
    (tmp_path / "CollectionState.json").write_text(json.dumps(coll), "utf-8-sig")
    assert load_current_deck(tmp_path) == ("B", ["Medusa", "Medusa"])


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _turn(turn, energy_max, deck, hand_n=3):
    s = snap(turn=turn)
    s["me"].update(max_energy=energy_max, energy=energy_max, deck_count=deck, ended_turn=turn - 1,
                   hand=[card(f"C{i}", 1, 1) for i in range(hand_n)])
    return s


def test_turn_gate_waits_for_energy_and_draw():
    clk = Clock()
    g = TurnGate(draw_timeout_s=5, settle_s=0.3, clock=clk)
    assert not g.ready(_turn(2, 1, 8))           # 新回合瞬间：能量还是上回合的
    clk.t = 1
    assert not g.ready(_turn(2, 2, 8))           # 能量补满，但还没抽牌
    clk.t = 2
    s = _turn(2, 2, 7, hand_n=4)                 # 抽到牌（牌库 8 -> 7）
    assert not g.ready(s)                        # 刚变化，未稳定
    clk.t = 2.4
    assert g.ready(s)                            # 稳定 0.4s -> 就绪


def test_turn_gate_draw_timeout_fallback():
    clk = Clock()
    g = TurnGate(draw_timeout_s=5, settle_s=0.3, clock=clk)
    s = _turn(3, 3, 6)                           # 中途附加：看不到抽牌
    assert not g.ready(s)
    clk.t = 4
    assert not g.ready(s)
    clk.t = 5.1
    assert g.ready(s)


def test_turn_gate_waits_for_real_hand_ids():
    """官网版 T1：手牌对象已在但 CardDefId 仍是占位 "None"，不能决策。"""
    clk = Clock()
    g = TurnGate(draw_timeout_s=5, settle_s=0.3, clock=clk)
    placeholder = snap(turn=1, me={**snap()["me"], "energy": 1, "max_energy": 1, "ended_turn": 0,
                                   "hand": [card("None", 0, 0, entity=i) for i in range(4)]})
    assert not g.ready(placeholder)
    clk.t = 10
    assert not g.ready(placeholder)              # 即使超过抽牌兜底时间也不放行
    real = snap(turn=1, me={**snap()["me"], "energy": 1, "max_energy": 1, "ended_turn": 0,
                            "hand": [card(c, 1, 1, entity=i) for i, c in enumerate(DECK[:4])]})
    g.ready(real)
    clk.t = 10.5
    assert g.ready(real)


def test_stakes_fields_map_to_game_state():
    s = snap(cube_value=1, cube_value_next=2, stakes_raised=1)
    s["me"].update(can_raise_stakes=False, snap_turns=[4])     # 我方 T4 Snap（实测 b03082ae）
    s["opp"].update(can_raise_stakes=False, snap_turns=[])     # 对手没 Snap，但 CanRaiseStakes 也是 False
    gs = to_game_state(s, DECK)
    assert gs.can_snap is False and gs.opp_snapped is False and gs.cube_next == 2
    s["me"].update(can_raise_stakes=True, snap_turns=[])
    s["opp"]["snap_turns"] = [3]                                # 对手 Snap
    gs = to_game_state(s, DECK)
    assert gs.can_snap is True and gs.opp_snapped is True
    s.pop("stakes_raised"); s["me"].pop("can_raise_stakes")                    # 字段缺失（如国际服未适配）：保守不 Snap
    assert to_game_state(s, DECK).can_snap is False


def _no_ghost_state(r, misses=0, miss_since=None):
    r._timer, r._timer_misses, r._timer_miss_since = 0, misses, miss_since
    r.ghost_timer_misses, r.ghost_after_s = 3, 25.0
    r._ghost_ids, r._ghost_cleared = {}, set()


def test_reader_holds_finished_game_without_rescanning():
    """对手撤退后：刚结束的对局在 hold 期内直接复用（不做 8-10s 的全堆重扫）。"""
    from perception.memory.snap import SnapMemoryReader

    r = SnapMemoryReader.__new__(SnapMemoryReader)
    r.game, r.game_id, r._last_scan, r.rescan_interval = 0x1000, "g" * 36, 0.0, 5.0
    r.finished_hold_s, r._finished_since, r._seen_live = 30.0, None, False
    r._hot, r._last_fast, r._stale_ids = set(), 0.0, None
    _no_ghost_state(r)
    finished = {"v": False}
    r._is_game = lambda g: True
    r._finished = lambda g: finished["v"]
    r.il = NS(get_str=lambda obj, name: r.game_id)
    scans = []
    r.find_games = lambda: scans.append(1) or []
    assert r.select_game() and not scans
    finished["v"] = True
    assert r.select_game() and r.select_game() and not scans     # 刚结束：复用，不重扫
    r._finished_since -= 31
    r.select_game()
    assert scans                                                  # hold 期过后才重扫找新对局


def test_executor_wait_aborts_on_game_over():
    from executor.actions import Executor, GameOver

    ex = Executor.__new__(Executor)
    ex.verify_timeout_s = 5
    ex.snapshot = lambda: {"finished": True, "opp": {"conceded": True}}
    import time as _t
    t = _t.time()
    with pytest.raises(GameOver, match="对手撤退"):
        ex._wait(lambda s: False)
    assert _t.time() - t < 0.5


def test_reader_does_not_hold_game_that_was_already_finished():
    """启动时选中的旧对局本来就已结束：不能 hold，否则新对局晚 30s 才被发现（实测 T1 几乎超时）。"""
    from perception.memory.snap import SnapMemoryReader

    r = SnapMemoryReader.__new__(SnapMemoryReader)
    r.game, r.game_id, r._last_scan, r.rescan_interval = 0x1000, "g" * 36, 0.0, 5.0
    r.finished_hold_s, r._finished_since, r._seen_live = 30.0, None, False
    r._hot, r._last_fast, r._stale_ids = set(), 0.0, None
    _no_ghost_state(r)
    r._is_game = lambda g: True
    r._finished = lambda g: True
    r.il = NS(get_str=lambda obj, name: r.game_id)
    scans = []
    r.find_games = lambda: scans.append(1) or []
    r.select_game()
    assert scans


def test_reader_ignores_ghost_game_without_turn_timer():
    """客户端内存残留的未结束旧对局（停在 T4、永不结束）：持续找不到回合计时器 -> 判幽灵、不再选；回合推进则恢复。"""
    import time as _t
    from perception.memory.snap import SnapMemoryReader

    ghost, other = "a" * 36, "b" * 36
    games = {"turn": 4}
    r = SnapMemoryReader.__new__(SnapMemoryReader)
    r.game, r.game_id, r._last_scan, r.rescan_interval = 0x1000, ghost, 0.0, 5.0
    r.finished_hold_s, r._finished_since, r._seen_live = 30.0, None, False
    r._hot, r._last_fast, r._stale_ids = set(), 0.0, None
    _no_ghost_state(r, misses=2, miss_since=_t.time() - 60)
    r._is_game = lambda g: True
    r._finished = lambda g: g != 0x1000
    r.il = NS(get_str=lambda obj, name: ghost if obj == 0x1000 else other, get_int=lambda obj, name: games["turn"])
    r._resolve_local = lambda vote=True: None
    r.find_games = lambda: [
        {"addr": 0x1000, "id": ghost, "turn": games["turn"], "finished": False, "client_state": True},
        {"addr": 0x2000, "id": other, "turn": 6, "finished": True, "client_state": True},
    ]
    assert r.select_game() and r.game_id == ghost      # 计时器缺席次数不够：仍是当前对局
    r._timer_misses = 3
    assert r.select_game() and r.game_id == other      # 判幽灵：退回已结束的旧局（= 不在对局中）
    assert ghost in r._ghost_ids
    games["turn"] = 5                                  # 回合推进：其实是真对局，恢复且不再判
    r._last_scan = 0.0
    assert r.select_game() and r.game_id == ghost
    assert ghost in r._ghost_cleared and not r._ghost_ids
    r._timer_misses, r._timer_miss_since = 9, _t.time() - 60
    assert r.select_game() and r.game_id == ghost

"""感知层：快照 -> GameState、剩余牌库推算、出牌阶段判断、卡组读取（不需要游戏进程）。"""
import json

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

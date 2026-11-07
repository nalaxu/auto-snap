"""场地利弊规则（玩家技巧）：利好场地多放；负面场地前期避开、最后一回合再放。"""
import json

import pytest

from brain import rule_based
from brain.locations import LocationAdvisor, LocationKB
from brain.models import Card, GameState, Location


@pytest.fixture
def advisor(tmp_path):
    kb = {
        "MuirIsland": {"effect": "End of Turn: Give cards here +1 Power.", "source": "screenshot",
                       "bias": {"early": 2, "mid": 1, "final": 0}, "tags": []},
        # 玩家举例：打出的牌会复制一张给对手 -> 前期不利，最后一回合再放
        "CopyToOpp": {"effect": "When you play a card here, your opponent gets a copy.", "source": "test",
                      "bias": {"early": -3, "mid": -2, "final": 0}, "tags": []},
        "OnslaughtsCitadel": {"effect": "Ongoing effects here are doubled.", "source": "screenshot",
                              "bias": {"early": 0, "mid": 0, "final": 0}, "tags": ["ongoing_double"]},
        "Ruins": {"effect": "", "source": "screenshot", "bias": {"early": 0, "mid": 0, "final": 0}, "tags": []},
    }
    p = tmp_path / "locations.json"
    p.write_text(json.dumps(kb), "utf-8")
    return LocationAdvisor(kb=LocationKB(p), unknown_path=tmp_path / "unknown.json")


def state(turn, names, hand, powers=None):
    powers = powers or [(0, 0)] * 3
    return GameState(turn=turn, energy=6, hand=hand,
                     locations=[Location(idx=i, name=n, my_power=m, opp_power=o) for i, (n, (m, o)) in enumerate(zip(names, powers))])


def test_favorable_location_preferred_early(advisor):
    s = state(2, ["Ruins", "MuirIsland", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    d = rule_based.decide(s, advisor)
    assert d.actions[0].location == 1
    assert "MuirIsland" in d.reason


def test_negative_location_avoided_early_but_ok_on_final_turn(advisor):
    names = ["CopyToOpp", "Ruins", "Ruins"]
    early = rule_based.decide(state(2, names, [Card(id="Hulk", cost=6, power=12)], [(0, 3), (0, 0), (0, 0)]), advisor)
    assert early.actions[0].location != 0          # 即使地点 0 落后可追平，前期也避开
    final = rule_based.decide(state(6, names, [Card(id="Hulk", cost=6, power=12)], [(0, 3), (0, 0), (0, 0)]), advisor)
    assert final.actions[0].location == 0          # 最后一回合负面影响最小，按争夺价值放


def test_ongoing_card_goes_to_citadel(advisor):
    s = state(5, ["Ruins", "OnslaughtsCitadel", "Ruins"], [Card(id="IronMan", cost=5, power=0)])
    assert rule_based.decide(s, advisor).actions[0].location == 1


def test_unknown_location_is_neutral_and_recorded(advisor, tmp_path):
    s = state(3, ["Ruins", "BrandNewPlace", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    rule_based.decide(s, advisor)
    assert "BrandNewPlace" in json.loads((tmp_path / "unknown.json").read_text("utf-8"))


def test_vanilla_card_goes_to_washington_dc(tmp_path):
    from brain.locations import LocationAdvisor, LocationKB  # 使用真实知识库（只读）
    adv = LocationAdvisor(kb=LocationKB(), unknown_path=tmp_path / "u.json")
    s = state(3, ["Ruins", "Ruins", "WashingtonDC"], [Card(id="Cyclops", cost=3, power=4)])
    d = rule_based.decide(s, adv)
    assert d.actions[0].location == 2 and "WashingtonDC" in d.reason
    s2 = state(3, ["Ruins", "Ruins", "WashingtonDC"], [Card(id="Quicksilver", cost=1, power=2)])
    assert "WashingtonDC" not in rule_based.decide(s2, adv).reason   # 有能力的牌不加成


def test_real_kb_machineworld_follows_player_tip(tmp_path):
    from brain.locations import LocationAdvisor, LocationKB
    adv = LocationAdvisor(kb=LocationKB(), unknown_path=tmp_path / "u.json")
    names = ["Machineworld", "Ruins", "Ruins"]
    early = rule_based.decide(state(2, names, [Card(id="Hulk", cost=6, power=12)], [(0, 3), (0, 0), (0, 0)]), adv)
    assert early.actions[0].location != 0
    final = rule_based.decide(state(6, names, [Card(id="Hulk", cost=6, power=12)], [(0, 3), (0, 0), (0, 0)]), adv)
    assert final.actions[0].location == 0

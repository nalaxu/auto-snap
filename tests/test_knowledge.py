"""资料库关联与出牌限制解析（使用仓库内 data/card_db.json、data/location_db.json）。"""
import json

from brain import knowledge, rule_based
from brain.llm.decider import build_prompt
from brain.locations import LocationAdvisor, LocationKB
from brain.models import Card, GameState, Location, TurnDecision, PlayAction
from brain.validator import validate


def st(turn, names, hand, opp=None, mine=None, powers=None):
    powers = powers or [(0, 0)] * 3
    locs = []
    for i, n in enumerate(names):
        locs.append(Location(idx=i, name=n, my_power=powers[i][0], opp_power=powers[i][1],
                             opp_cards=(opp or {}).get(i, []), my_cards=(mine or {}).get(i, [])))
    return GameState(turn=turn, energy=6, hand=hand, locations=locs)


def test_db_covers_locations_seen_in_games():
    db = knowledge.default_db()
    seen = ["SinisterLondon", "DreamDimension", "MirrorDimension", "Krakoa", "Limbo", "KunLun", "XMansion",
            "CentralPark", "Wakanda", "Ebonshire", "Elysium", "ShurisLab"]
    assert all(db.location_effect(n) for n in seen)
    assert "Nobody can play cards that cost 4, 5, or 6 here" in db.ability("Goose")   # 国服库优先（国际服是 4 or more）
    # 人工维护的 cards.json 优先（国服实测 Hawkeye +3）
    assert "+3 Power" in db.ability("Hawkeye")


def test_goose_blocks_high_cost_for_both_players():
    goose = Card(id="Goose", cost=2, power=2)
    s = st(6, ["Ruins", "Ruins", "Ruins"], [Card(id="Gamora", cost=5, power=8), Card(id="Hawkeye", cost=1, power=1)],
           opp={0: [goose]})
    errs = validate(s, TurnDecision(actions=[PlayAction(card="Gamora", location=0)]))
    assert errs and "限制" in errs[0]
    assert not validate(s, TurnDecision(actions=[PlayAction(card="Hawkeye", location=0)]))
    d = rule_based.decide(s)
    assert all(not (a.card == "Gamora" and a.location == 0) for a in d.actions)


def test_location_text_restrictions():
    big = st(3, ["TheBigHouse", "Ruins", "Ruins"], [Card(id="TheThing", cost=4, power=6)])
    assert validate(big, TurnDecision(actions=[PlayAction(card="TheThing", location=0)]))
    crimson = st(3, ["CrimsonCosmos", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    assert validate(crimson, TurnDecision(actions=[PlayAction(card="Cyclops", location=0)]))
    kyln5 = st(5, ["Kyln", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    assert validate(kyln5, TurnDecision(actions=[PlayAction(card="Cyclops", location=0)]))
    kyln4 = st(4, ["Kyln", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    assert not validate(kyln4, TurnDecision(actions=[PlayAction(card="Cyclops", location=0)]))
    # 国服实测覆盖：Sanctum Sanctorum 可以出牌
    sanctum = st(3, ["SanctumSanctorum", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)])
    assert not validate(sanctum, TurnDecision(actions=[PlayAction(card="Cyclops", location=0)]))


def test_self_restrictions():
    s = st(3, ["Ruins", "Ruins", "Ruins"], [Card(id="Giganto", cost=6, power=10)])
    assert validate(s, TurnDecision(actions=[PlayAction(card="Giganto", location=1)]))
    assert not validate(s, TurnDecision(actions=[PlayAction(card="Giganto", location=0)]))
    cb = st(3, ["Ruins", "Ruins", "Ruins"], [Card(id="Crossbones", cost=2, power=5)], powers=[(3, 1), (0, 2), (0, 0)])
    assert not validate(cb, TurnDecision(actions=[PlayAction(card="Crossbones", location=0)]))
    assert validate(cb, TurnDecision(actions=[PlayAction(card="Crossbones", location=1)]))


def test_ebony_maw_only_blocks_owner():
    maw = Card(id="EbonyMaw", cost=1, power=7)
    mine = st(3, ["Ruins", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)], mine={0: [maw]})
    theirs = st(3, ["Ruins", "Ruins", "Ruins"], [Card(id="Cyclops", cost=3, power=4)], opp={0: [maw]})
    act = TurnDecision(actions=[PlayAction(card="Cyclops", location=0)])
    assert validate(mine, act) and not validate(theirs, act)


def test_kb_falls_back_to_db_effect(tmp_path):
    kb = LocationKB(tmp_path / "none.json")
    info = kb.get("SinisterLondon")
    assert info and info.source == "db" and "copy" in info.effect and info.bias_at(2, 6) == 0
    assert kb.get("BrandNewPlace") is None


def test_prompt_links_db_details(tmp_path):
    adv = LocationAdvisor(kb=LocationKB(tmp_path / "none.json"), unknown_path=tmp_path / "u.json")
    s = st(6, ["SinisterLondon", "DreamDimension", "Ruins"], [Card(id="Gamora", cost=5, power=8)],
           opp={0: [Card(id="Goose", cost=2, power=2)]})
    s.deck_remaining = ["Hulk"]
    data = json.loads(build_prompt(s, advisor=adv).split("\n", 1)[1])
    assert "copy" in data["locations"][0]["kb"]["effect"]
    assert data["locations"][0]["opp_cards_revealed"] == ["Goose"]
    assert "cost 4, 5, or 6" in data["card_info"]["Goose"]
    assert "Hulk" in data["card_info"] and data["hand"][0]["ability"]


def test_goose_restriction_both_wordings():
    for text, blocked, free in (("Ongoing: Nobody can play cards that cost 4 or more here.", [4, 5, 6, 7], [3]),
                                ("Ongoing: Nobody can play cards that cost 4, 5, or 6 here.", [4, 5, 6], [3, 7])):
        checks = knowledge.parse_restrictions(text)
        assert checks
        assert all(any(c.blocks(6, n, 1) for c in checks) for n in blocked)
        assert not any(c.blocks(6, n, 1) for c in checks for n in free)

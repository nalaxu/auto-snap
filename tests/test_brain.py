from brain import rule_based
from brain.models import Card, GameState, Location, PlayAction, TurnDecision
from brain.validator import validate


def _state(**kw) -> GameState:
    base = dict(
        turn=3,
        energy=3,
        hand=[Card(id="a", cost=1, power=2), Card(id="b", cost=2, power=3), Card(id="c", cost=3, power=5)],
        locations=[Location(idx=0, opp_power=4), Location(idx=1), Location(idx=2, my_power=10)],
    )
    base.update(kw)
    return GameState(**base)


def test_validator_rejects_over_energy():
    d = TurnDecision(actions=[PlayAction(card="c", location=0), PlayAction(card="a", location=1)])
    assert any("超过剩余能量" in e for e in validate(_state(), d))


def test_validator_rejects_full_location():
    full = Location(idx=0, my_cards=[Card(id=f"x{i}", cost=0, power=0) for i in range(4)])
    s = _state(locations=[full, Location(idx=1), Location(idx=2)])
    d = TurnDecision(actions=[PlayAction(card="a", location=0)])
    assert any("已满" in e for e in validate(s, d))


def test_rule_based_is_always_valid_and_uses_energy():
    s = _state()
    d = rule_based.decide(s)
    assert validate(s, d) == []
    assert sum(next(c.cost for c in s.hand if c.id == a.card) for a in d.actions) == 3
    assert d.actions[0].location == 0  # 落后 4 的地点被 5 战力追平


def _loc_state(names, turn, hand):
    return GameState(turn=turn, energy=6, hand=hand,
                     locations=[Location(idx=i, name=n) for i, n in enumerate(names)])


def test_avengers_compound_forces_location_on_turn_5():
    """实机：T5 Avengers Compound 要求所有牌打在这里，规则 AI 曾打到地点 0 被拒。"""
    s = _loc_state(["StrangeAcademy", "HellfireClub", "AvengersCompound"], 5, [Card(id="IronMan", cost=5, power=0)])
    d = rule_based.decide(s)
    assert [(a.card, a.location) for a in d.actions] == [("IronMan", 2)]
    bad = TurnDecision(actions=[PlayAction(card="IronMan", location=0)])
    assert any("限制" in e for e in validate(s, bad))
    s6 = _loc_state(["StrangeAcademy", "HellfireClub", "AvengersCompound"], 6, [Card(id="IronMan", cost=5, power=0)])
    assert validate(s6, bad) == []  # 只限第 5 回合


def test_hellfire_club_blocks_one_cost_and_forbidden_list():
    s = _loc_state(["HellfireClub", "Ruins", "Ruins"], 3, [Card(id="Misty", cost=1, power=2)])
    assert validate(s, TurnDecision(actions=[PlayAction(card="Misty", location=0)]))
    s.forbidden = [("Misty", 1)]  # 运行时被拒过
    assert [a.location for a in rule_based.decide(s).actions] == [2]


def test_validator_allows_duplicate_copies_in_hand():
    """实机 T6：手牌两张 Sentinel，大模型两张都打被误判「重复打出」。"""
    s = GameState(turn=6, energy=2, hand=[Card(id="Sentinel", cost=1, power=3, hand_index=0),
                                          Card(id="Sentinel", cost=1, power=3, hand_index=1)],
                  locations=[Location(idx=i) for i in range(3)])
    two = TurnDecision(actions=[PlayAction(card="Sentinel", location=0), PlayAction(card="Sentinel", location=2)])
    assert validate(s, two) == []
    three = TurnDecision(actions=two.actions + [PlayAction(card="Sentinel", location=1)])
    assert any("只有 2 张" in e for e in validate(s, three))

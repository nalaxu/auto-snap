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


def _board_state(turn, hand, my_cards=None, played_last_turn=None):
    from brain.models import GameState, Location

    locs = [Location(idx=i, name="", revealed=False) for i in range(3)]
    for i, c in (my_cards or []):
        locs[i].my_cards.append(c)
    return GameState(turn=turn, energy=turn, hand=hand, locations=locs, played_last_turn=played_last_turn)


def test_infinaut_needs_empty_last_turn():
    """用户说明（e11bf2c5 T6）：上回合出过牌就不能打 Infinaut。"""
    from brain.location_rules import can_play
    from brain.models import Card

    inf = Card(id="Infinaut", cost=6, power=20)
    assert not can_play(_board_state(6, [inf], played_last_turn=True), inf, 2)
    assert can_play(_board_state(6, [inf], played_last_turn=False), inf, 2)


def test_agatha_blocks_plays_on_even_turns():
    from brain.location_rules import can_play
    from brain.models import Card

    hand = [Card(id="AgathaHarkness", cost=6, power=12), Card(id="Medusa", cost=2, power=2)]
    assert not can_play(_board_state(6, hand), hand[1], 1)
    assert can_play(_board_state(5, hand), hand[1], 1)


def test_activatable_cards_and_validation():
    """激活：打出当回合不能激活；之后的回合可激活一次；非激活牌不能激活。"""
    from brain.models import ActivateAction, Card, TurnDecision
    from brain.validator import activatable, validate

    danger = Card(id="Danger", cost=4, power=6, entity=11, turn_played=4, activated=False, slot=0)
    panther = Card(id="BlackPanther", cost=5, power=4, entity=12, turn_played=5, activated=False, slot=1)
    st = _board_state(5, [], my_cards=[(1, danger), (1, panther)])
    assert [c.id for c in activatable(st, st.locations[1])] == ["Danger"]
    assert not validate(st, TurnDecision(activate=[ActivateAction(card="Danger", location=1)]))
    assert validate(st, TurnDecision(activate=[ActivateAction(card="BlackPanther", location=1)]))
    st4 = _board_state(4, [], my_cards=[(1, danger.model_copy(update={"turn_played": 4}))])
    assert not activatable(st4, st4.locations[1])                     # 本回合刚打出
    danger.activated = True
    assert not activatable(st, st.locations[1])                       # 已激活过


def test_board_slot_positions():
    from executor.layout import board_slot_pos

    assert board_slot_pos(1, 0)[0] < 0.5 < board_slot_pos(1, 1)[0]
    assert board_slot_pos(2, 2)[1] > board_slot_pos(2, 0)[1]

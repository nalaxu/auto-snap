"""场上要点（消息栈）与规则 AI 的卡牌配合。场景取自实战对局 3ac649a8（T3 Hawkeye 跟牌、T4 Medusa 放中路）。"""
from brain import rule_based
from brain.board import BoardTracker
from brain.models import Card, GameState, Location


def loc(i, name="Ruins", mine=(), opp=(), mp=0, op=0):
    return Location(idx=i, name=name, my_cards=list(mine), opp_cards=list(opp), opp_card_count=len(opp),
                    my_power=mp, opp_power=op)


def hawkeye(e=1):
    return Card(id="Hawkeye", cost=1, power=1, entity=e)


def test_hawkeye_followup_note_appears_once_then_expires():
    tr = BoardTracker()
    t2 = GameState(turn=2, energy=2, hand=[], locations=[loc(0), loc(1), loc(2)])
    tr.update("g", t2)
    t3 = GameState(turn=3, energy=3, hand=[], locations=[loc(0), loc(1), loc(2, mine=[hawkeye()], mp=1)])
    tr.update("g", t3)
    assert any("本回合有效" in n and "Hawkeye" in n and "+3" in n for n in t3.board_notes)
    t4 = GameState(turn=4, energy=4, hand=[], locations=[loc(0), loc(1), loc(2, mine=[hawkeye()], mp=1)])
    tr.update("g", t4)
    assert not any("Hawkeye" in n for n in t4.board_notes)   # 过了触发回合即删除


def test_notes_ongoing_opp_new_and_location_events():
    tr = BoardTracker()
    tr.update("g", GameState(turn=3, energy=3, hand=[], locations=[loc(0), loc(1), loc(2)]))
    goose = Card(id="Goose", cost=2, power=2, entity=9)
    falcon = Card(id="Falcon", cost=2, power=3, entity=8)
    s = GameState(turn=4, energy=4, hand=[],
                  locations=[loc(0, opp=[goose]), loc(1, opp=[falcon]), loc(2, name="Mindscape")])
    tr.update("g", s)
    text = "\n".join(s.board_notes)
    assert "【持续】" in text and "Goose" in text and "cost 4 or more" in text
    assert "【对手上回合】" in text and "Falcon" in text
    assert "【场地事件·2 回合后】" in text and "swap hands" in text
    s6 = GameState(turn=7, energy=6, hand=[], locations=[loc(0, opp=[goose]), loc(1, opp=[falcon]), loc(2, name="Mindscape")])
    tr.update("g", s6)
    assert "Mindscape" not in "\n".join(s6.board_notes) and "Falcon" not in "\n".join(s6.board_notes)


def test_rule_ai_follows_up_hawkeye_with_iron_man():
    """实战 T3：右路有上回合的 Hawkeye + Cyclops，应把 Iron Man 打到右路（触发 +3 且翻倍），而不是中路。"""
    tr = BoardTracker()
    tr.update("g", GameState(turn=2, energy=2, hand=[], locations=[loc(0), loc(1), loc(2)]))
    s = GameState(turn=3, energy=4, hand=[Card(id="Starlord", cost=1, power=2), Card(id="IronMan", cost=4, power=0),
                                          Card(id="Medusa", cost=1, power=2)],
                  locations=[loc(0, op=1, opp=[Card(id="RealityStone", cost=1, power=1, entity=5)]),
                             loc(1, mine=[Card(id="Quicksilver", cost=1, power=2, entity=2)], mp=2, op=2,
                                 opp=[Card(id="Nightcrawler", cost=1, power=2, entity=6)]),
                             loc(2, mine=[hawkeye(3), Card(id="Cyclops", cost=2, power=5, entity=4)], mp=6)])
    tr.update("g", s)
    d = rule_based.decide(s)
    assert ("IronMan", 2) in [(a.card, a.location) for a in d.actions], d.reason


def test_rule_ai_keeps_middle_slot_for_medusa():
    """实战 T4：中路只剩 2 个空位，逐张贪心会被 Punisher/Star-Lord 占满；整手搜索应把 Medusa 放中路。"""
    mid = [Card(id="IronMan", cost=5, power=0, entity=1), Card(id="Quicksilver", cost=1, power=2, entity=2)]
    s = GameState(turn=4, energy=4, hand=[Card(id="Starlord", cost=1, power=2), Card(id="Medusa", cost=1, power=2),
                                          Card(id="Punisher", cost=2, power=3)],
                  locations=[loc(0, op=10), loc(1, mine=mid, mp=4, op=4), loc(2, mine=[hawkeye(3)], mp=6)])
    d = rule_based.decide(s)
    assert ("Medusa", 1) in [(a.card, a.location) for a in d.actions], d.reason

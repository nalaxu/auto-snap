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
    assert "【持续】" in text and "Goose" in text and "cost 4, 5, or 6" in text
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


def test_shuri_next_card_note_and_reorder():
    """用户复盘（ee4fc27a T5）：T4 Shuri 在右路，T5 先打了左路 Quinjet，Weapon H 没有被加倍。"""
    from brain.board import build_notes, reorder_for_next_card
    from brain.knowledge import default_db
    from brain.models import Card, GameState, Location, PlayAction, TurnDecision

    shuri = Card(id="Shuri", cost=4, power=3, entity=5, turn_played=4)
    locs = [Location(idx=0, name="VibraniumMines"), Location(idx=1, name="Madripoor"), Location(idx=2, name="ZennLa", my_cards=[shuri])]
    st = GameState(turn=5, energy=5, locations=locs,
                   hand=[Card(id="Quinjet", cost=1, power=1), Card(id="WeaponH", cost=4, power=14)])
    notes = build_notes(st, default_db())
    assert any("放牌顺序" in n and "Shuri" in n for n in notes)
    d = TurnDecision(actions=[PlayAction(card="Quinjet", location=0), PlayAction(card="WeaponH", location=2)])
    assert reorder_for_next_card(st, d)
    assert [a.card for a in d.actions] == ["WeaponH", "Quinjet"]


def test_location_move_activation_and_tempo_notes():
    """用户复盘（88c25f44）：Kvch 给的 Fan Fei 没激活、New York 第 6 回合可移动没用上；要点栈要列出可做的事。"""
    from brain.board import build_notes
    from brain.knowledge import default_db
    from brain.location_rules import move_targets
    from brain.models import Card, GameState, Location, MoveAction, TurnDecision
    from brain.validator import validate

    fanfei = Card(id="FanFei", cost=1, power=1, entity=3, turn_played=2, activated=False, slot=0)
    kah = Card(id="Kahhori", cost=4, power=7, entity=4, turn_played=4, activated=False, slot=1)
    spidey = Card(id="SpiderMan", cost=2, power=4, entity=5, turn_played=3, activated=False, slot=0)

    def st(turn):
        return GameState(turn=turn, energy=turn, hand=[], opp_destroyed=3, opp_energy_by_turn={4: 4, 5: 3},
                         locations=[Location(idx=0, name="TinkerersWorkshop"),
                                    Location(idx=1, name="Kvch", my_cards=[fanfei, kah]),
                                    Location(idx=2, name="NewYork", my_cards=[spidey])])

    s6 = st(6)
    notes = build_notes(s6, default_db())
    assert any("可激活·最后机会" in n and "FanFei" in n for n in notes)
    assert any("可移动·本回合" in n and "NewYork" in n and "Kahhori" in n for n in notes)
    assert any("对手节奏" in n and "T5 3/5" in n and "摧毁 combo 3 次" in n for n in notes)
    assert move_targets(s6, kah, 1) == [2] and move_targets(st(5), kah, 1) == []      # 只有第 6 回合能移到 New York
    assert not validate(s6, TurnDecision(moves=[MoveAction(card="Kahhori", from_location=1, to_location=2)]))
    assert validate(st(5), TurnDecision(moves=[MoveAction(card="Kahhori", from_location=1, to_location=2)]))


def test_castle_zemo_order_signal():
    """用户复盘（773d5c5d T6）：Castle Zemo 下一张打到这里的牌换到对手一侧 -> 先放最弱的（Agent 13 而不是 Coulson）。"""
    from brain.board import BoardTracker, reorder_for_next_card
    from brain.models import Card, GameState, Location, PlayAction, TurnDecision

    st = GameState(turn=6, energy=6, locations=[Location(idx=0, name="Superflow"), Location(idx=1, name="CastleZemo"),
                                                Location(idx=2, name="Vault")],
                   hand=[Card(id="AgentCoulson", cost=3, power=4), Card(id="IronPatriot", cost=2, power=3),
                         Card(id="Agent13", cost=1, power=2)])
    tracker = BoardTracker()
    tracker.update("g", st)
    assert any("放牌顺序" in n and "CastleZemo" in n and "最弱" in n for n in st.board_notes)
    d = TurnDecision(actions=[PlayAction(card="AgentCoulson", location=1), PlayAction(card="IronPatriot", location=1),
                              PlayAction(card="Agent13", location=1)])
    assert reorder_for_next_card(st, d)
    assert d.actions[0].card == "Agent13"
    tracker.record_play(6, 1)                     # 揭示后往那里打过牌：信号已触发，下回合不再提示
    st.turn = 7
    tracker.update("g", st)
    assert not any("CastleZemo" in n and "放牌顺序" in n for n in st.board_notes)


def test_signal_classification_from_text(tmp_path):
    """未收录的牌 / 场地：按文字自动识别并写入知识库。"""
    import json

    from brain.signals import SignalKB, classify

    assert classify("The next card you play here switches sides.") == {"scope": "here", "polarity": "bad"}
    assert classify("On Reveal: If you play your next card at this location, double its Power.")["scope"] == "global_here"
    assert classify("On Reveal: Give the next card you play +2 Power.") == {"scope": "global", "polarity": "good"}
    assert classify("On Reveal: Remove the abilities on the next card you play.")["polarity"] == "bad"
    assert classify("Cards here have -1 Power.") is None
    kb = SignalKB(tmp_path / "signals.json")
    assert kb.get("cards", "Forge", "On Reveal: Give the next card you play +2 Power.")["source"] == "auto"
    assert "Forge" in json.loads((tmp_path / "signals.json").read_text("utf-8"))["cards"]


def test_signal_enemy_side_is_bad():
    from brain.signals import classify

    assert classify("The next card you play here pulls a card to the enemy side from their hand.")["polarity"] == "bad"

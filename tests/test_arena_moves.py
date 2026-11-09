"""2026-11-09 竞技场实战反馈：Cap's Shield 由 Sam Wilson 赋予移动、移走后空位可出牌、夜魔侠第 5 回合观察。"""
from __future__ import annotations

from brain.location_rules import daredevil_watch, granted_movers, move_targets
from brain.models import Card, GameState, Location, MoveAction, PlayAction, TurnDecision
from brain.validator import validate


def c(cid: str, turn: int | None = None, cost: int = 0, power: int = 0, slot: int | None = None) -> Card:
    return Card(id=cid, cost=cost, power=power, turn_played=turn, slot=slot)


def kunlun_t6() -> GameState:
    """对局 0fb46d5d 第 6 回合：左路 K'un-Lun 我方 4 张（满），Shield 在左，Sam Wilson（Cap）在右。"""
    return GameState(
        turn=6, max_turn=6, energy=6,
        hand=[Card(id="Odin", cost=6, power=8, hand_index=0), Card(id="X23", cost=1, power=2, hand_index=1)],
        locations=[
            Location(idx=0, name="KunLun", my_cards=[c("Wolverine", 4), c("CapsShield", 0), c("Ironheart", 5), c("Deathlok", 3)]),
            Location(idx=1, name="SpiderIsland", my_cards=[]),
            Location(idx=2, name="BrooklynBridge", my_cards=[c("Tarantula", 4), c("Echo", 5), c("SamWilson", 2)]),
        ],
    )


def test_sam_wilson_grants_caps_shield_moves():
    st = kunlun_t6()
    assert "CapsShield" in granted_movers(st)
    shield = st.locations[0].my_cards[1]
    assert set(move_targets(st, shield, 0)) == {1, 2}
    wolverine = st.locations[0].my_cards[0]
    assert move_targets(st, wolverine, 0) == []


def test_no_grant_when_sam_wilson_just_played():
    st = kunlun_t6()
    st.locations[2].my_cards[2].turn_played = 6     # 本回合刚打出、还没揭示
    assert "CapsShield" not in granted_movers(st)


def test_move_frees_slot_for_play_in_same_turn():
    st = kunlun_t6()
    d = TurnDecision(actions=[PlayAction(card="Odin", location=0)],
                     moves=[MoveAction(card="CapsShield", from_location=0, to_location=2)])
    assert validate(st, d) == []
    # 不移走就打进满员的左路：不合法
    assert any("已满" in e for e in validate(st, TurnDecision(actions=[PlayAction(card="Odin", location=0)])))


def test_move_into_full_location_rejected():
    st = kunlun_t6()
    st.locations[2].my_cards.append(c("Brood", 4))   # 右路也满
    d = TurnDecision(moves=[MoveAction(card="CapsShield", from_location=0, to_location=2)])
    assert validate(st, d)        # 目标地点满员：不能移入


def test_daredevil_watch_only_turn5_revealed():
    st = kunlun_t6()
    st.turn = 5
    assert not daredevil_watch(st)
    st.locations[1].my_cards.append(c("Daredevil", 2))
    assert daredevil_watch(st)
    st.turn = 6
    assert not daredevil_watch(st)
    st.turn = 5
    st.locations[1].my_cards[0].turn_played = 5      # 本回合才打出：不触发
    assert not daredevil_watch(st)

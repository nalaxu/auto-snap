"""竞技场 M3：整场期望（档位价值、输的代价、撤退线、Snap 门槛）与对局提示词注入。"""
from __future__ import annotations

import pytest

from brain.arena_strategy import (ArenaContext, ArenaModel, loss_value, retreat_check, snap_veto, value,
                                  win_value)
from brain.models import GameState, Location

M = ArenaModel()


def test_terminal_is_tier_step_plus_avatar():
    assert M.terminal(14) == pytest.approx(3.5 + 14 * M.avatar_eps)
    assert M.terminal(15) == pytest.approx(6.0 + 15 * M.avatar_eps)
    assert M.terminal(-20) == 0.0
    assert M.tier_of(13) == (10, 15)
    assert M.tier_of(40) == (35, None)


def test_loss_rules_hp_then_halved_overflow():
    # 血够：只扣血，继续打
    assert loss_value(M, 4, 6, 4) == pytest.approx(value(M, 4, 2))
    # 血量恰好扣到 0：本场结束，不没收
    assert loss_value(M, 12, 4, 4) == pytest.approx(M.terminal(12))
    # 剩 1 血输 8：超出 7 全额没收（实测对局 6961a4a4：积分 12 → 5）
    assert loss_value(M, 12, 1, 8) == pytest.approx(M.terminal(5))
    # 剩 6 血输 8：超出 2，全额没收 2（实测对局 9de0c74b：积分 10 → 8）
    assert loss_value(M, 10, 6, 8) == pytest.approx(M.terminal(8))


def test_win_reaching_cap_ends_run():
    assert win_value(M, 34, 5, 8) == pytest.approx(M.terminal(42))


def test_low_hp_makes_snap_cheaper():
    full = ArenaContext(score=4, hp=10).breakeven(2, 4)
    low = ArenaContext(score=4, hp=1).breakeven(2, 4)
    assert low < full
    assert low == pytest.approx(M.snap_floor)     # 绝境且积分少（没什么可被没收）：门槛降到下限
    # 积分多时超额全额没收很疼：剩 1 血、12 分时 2→4 的门槛反而高
    assert ArenaContext(score=12, hp=1).breakeven(2, 4) > full


def test_tier_awareness_near_threshold():
    # 33 分：赢 2 或赢 4 都到 35 封顶，Snap（2→4）几乎没有收益，门槛明显高于平时
    near = ArenaContext(score=33, hp=10).breakeven(2, 4)
    mid = ArenaContext(score=6, hp=10).breakeven(2, 4)
    assert near > mid + 0.1


def test_retreat_cutoff_floor_and_full_hp_close_to_ranked():
    c = ArenaContext(score=0, hp=10)
    assert 0.15 <= c.retreat_cutoff(2, 4) <= 0.35      # 血量充足时接近排位的 25%
    assert ArenaContext(score=4, hp=1).retreat_cutoff(2, 4) == pytest.approx(M.retreat_floor)


def make_state(**kw) -> GameState:
    base = dict(turn=6, max_turn=6, energy=6, cube_stake=2, cube_next=4, hand=[],
                locations=[Location(idx=i, name="", revealed=True) for i in range(3)])
    base.update(kw)
    return GameState(**base)


def test_retreat_check_only_on_final_or_opp_snap():
    ctx = ArenaContext(score=0, hp=10)
    assert retreat_check(make_state(turn=4), 0.05, ctx) == (False, "")
    yes, why = retreat_check(make_state(), 0.05, ctx)
    assert yes and "撤退线（正常）" in why
    assert retreat_check(make_state(), 0.9, ctx)[0] is False


def test_retreat_lines_normal_30_opp_snap_45():
    ctx = ArenaContext(score=3, hp=10)
    # 正常（最后一回合、对手没 Snap）：低于 30% 撤退
    assert retreat_check(make_state(), 0.29, ctx)[0] is True
    assert retreat_check(make_state(), 0.31, ctx)[0] is False
    # 对手 Snap 当回合（T5 Snap，T6 看到；对局 e4971723 校准胜率 39.5%）：低于 45% 撤退
    yes, why = retreat_check(make_state(opp_snapped=True, opp_snap_turn=5), 0.395, ctx)
    assert yes and "对手 Snap 当回合" in why
    assert retreat_check(make_state(opp_snapped=True, opp_snap_turn=5), 0.46, ctx)[0] is False
    # 对手早先（T3）Snap、最后一回合不是 Snap 当回合：按正常线 30%
    assert retreat_check(make_state(opp_snapped=True, opp_snap_turn=3), 0.35, ctx)[0] is False
    assert retreat_check(make_state(opp_snapped=True, opp_snap_turn=3), 0.25, ctx)[0] is True
    # 对手刚 Snap 的中期回合（T4 起）：45%
    assert retreat_check(make_state(turn=4, cube_stake=2, cube_next=2, opp_snapped=True, opp_snap_turn=4),
                         0.44, ctx)[0] is True
    # 对手前 3 回合 Snap（对局 4b495789：T1 Snap，胜率 48%）：只按整场期望线，不撤
    ctx6 = ArenaContext(score=4, hp=6)
    yes, why = retreat_check(make_state(turn=1, cube_stake=1, cube_next=2, opp_snapped=True, opp_snap_turn=1), 0.48, ctx6)
    assert not yes and "只用整场期望" in why
    assert retreat_check(make_state(turn=3, cube_stake=2, cube_next=2, opp_snapped=True, opp_snap_turn=3),
                         0.05, ctx6)[0] is True
    # 对手早先 Snap、中期非刚 Snap 的回合：不判断
    assert retreat_check(make_state(turn=5, opp_snapped=True, opp_snap_turn=3), 0.1, ctx) == (False, "")
    # 设置覆盖（面板「设置 → 竞技场」）
    m = ArenaModel.from_settings({"arena": {"retreat_final": 0.2, "retreat_opp_snap": 0.6, "retreat_opp_snap_from": 2}})
    c2 = ArenaContext(score=3, hp=10, model=m)
    assert retreat_check(make_state(), 0.25, c2)[0] is False
    assert retreat_check(make_state(opp_snapped=True, opp_snap_turn=5), 0.55, c2)[0] is True
    assert retreat_check(make_state(turn=2, cube_stake=2, cube_next=2, opp_snapped=True, opp_snap_turn=2),
                         0.55, c2)[0] is True


def test_snap_veto():
    ctx = ArenaContext(score=13, hp=10)
    assert snap_veto(0.4, ctx, 2, final_turn=False)[0] is True
    assert snap_veto(0.95, ctx, 2, final_turn=False)[0] is False
    assert snap_veto(None, ctx, 2, final_turn=False) == (False, "")


def test_prompt_contains_arena_context_not_deck_kb():
    from brain.llm.decider import build_prompt

    ctx = ArenaContext(score=4, hp=6)
    st = make_state(turn=3, arena=ctx.prompt(1, final_turn=False))
    text = build_prompt(st)
    assert '"arena"' in text and '"hp": 6' in text and "opp_hp" not in text and "snap_breakeven" in text
    assert "deck_notes" not in text


def test_model_from_settings():
    m = ArenaModel.from_settings({"arena": {"tier_value": {"0": 1, "15": 9}, "game_win_rate": 0.55, "snap_floor": 0.3}})
    assert m.terminal(15) == pytest.approx(9 + 15 * m.avatar_eps)
    assert m.win_rate == 0.55 and m.snap_floor == 0.3


def test_quickdraw_moveable_not_self_moveable():
    from brain.location_rules import self_moveable_text

    assert self_moveable_text("Your cards reveal here with +1 Power. Moveable twice.")
    assert not self_moveable_text("End of Turn: If you used a Moveable ability, give a Moveable card -1 Cost. Quickdraw:Moveable once.")
    assert not self_moveable_text("On Reveal: Draw a card.")

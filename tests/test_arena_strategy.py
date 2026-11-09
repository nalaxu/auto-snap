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
    # 剩 1 血输 8：超出 7 折半（向上取整 4）从积分没收（用户例子：扣 4 分）
    assert loss_value(M, 12, 1, 8) == pytest.approx(M.terminal(8))


def test_win_reaching_cap_ends_run():
    assert win_value(M, 34, 5, 8) == pytest.approx(M.terminal(42))


def test_low_hp_makes_snap_cheaper():
    full = ArenaContext(score=4, hp=10).breakeven(4, 8)
    low = ArenaContext(score=4, hp=1).breakeven(4, 8)
    assert low < full
    assert low == pytest.approx(M.snap_floor)     # 绝境：门槛降到下限


def test_tier_awareness_near_threshold():
    # 13 分：赢 2 或赢 4 都到 15 档，Snap（2→4）收益小，门槛高
    near = ArenaContext(score=13, hp=10).breakeven(2, 4)
    mid = ArenaContext(score=6, hp=10).breakeven(2, 4)
    assert near > mid


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
    assert yes and "整场期望撤退线" in why
    assert retreat_check(make_state(), 0.9, ctx)[0] is False


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

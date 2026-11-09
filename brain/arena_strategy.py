"""竞技场整场期望：按「血量 + 积分 + 宝箱档位」算单局里 Snap / 撤退的价值（docs/arena-mode-plan.md「M3」）。

规则（用户确认）：
- 赢 c 魔方：积分 +c；积分 ≥ 35 自动结束，按 35 档结算，超出部分计入下一个头像进度。
- 输 c 魔方且 c < 血量：扣 c 血，继续打。
- 输 c 魔方且 c ≥ 血量：血量归零、本场结束；超出部分 (c − 血量) 折半从积分没收（取整规则未知，按向上取整）。
- 撤退同样按当前赌注扣血 / 没收。

价值：终局价值 U(积分) = 宝箱档位价值（阶梯，用户决定以档位为主）+ 头像进度小权重线性项。
继续打下去的价值 V(积分, 血量) 用一个简化的整场模型递推：之后每局胜率 q、赌注分布 STAKE_DIST，
赢了积分增加、输了扣血或结束。这样「每点血值多少分」不需要拍脑袋，由档位和剩余血量自然算出：
血量充足时输的代价接近按魔方计，血量见底时超出部分只算一半，于是绝境更敢 Snap（以小博大）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

DEFAULT_TIERS = {-8: 0.0, 0: 1.0, 5: 2.0, 10: 3.5, 15: 6.0, 20: 7.0, 25: 8.0, 30: 11.0, 35: 14.0}
AVATAR_EPS = 0.05                 # 头像进度每分的价值（只用来打破平局）
STAKE_DIST = {1: 0.15, 2: 0.35, 4: 0.35, 8: 0.15}   # 一局最终输赢魔方数的分布（初值，积累竞技场对局后校准）


@dataclass(frozen=True)
class ArenaModel:
    tiers: tuple = tuple(sorted(DEFAULT_TIERS.items()))
    max_score: int = 35
    win_rate: float = 0.5          # 之后每局的胜率（初值 50%，积累后校准）
    stake_dist: tuple = tuple(sorted(STAKE_DIST.items()))
    avatar_eps: float = AVATAR_EPS
    # 下限（用户经验）：档位是阶梯，算出来的线在某些点会接近 0；胜算很低时即使血少也撤退保分，绝境 Snap 也要有起码的翻盘机会
    retreat_floor: float = 0.10
    snap_floor: float = 0.25

    @classmethod
    def from_settings(cls, settings: dict | None, max_score: int = 35) -> "ArenaModel":
        a = (settings or {}).get("arena", {}) or {}
        tiers = a.get("tier_value") or {}
        t = {int(k): float(v) for k, v in tiers.items()} if tiers else DEFAULT_TIERS
        return cls(tiers=tuple(sorted(t.items())), max_score=max_score,
                   win_rate=float(a.get("game_win_rate", 0.5)),
                   retreat_floor=float(a.get("retreat_floor", 0.10)), snap_floor=float(a.get("snap_floor", 0.25)))

    # ------------------------------------------------------------ 终局价值
    def terminal(self, score: int) -> float:
        """本场以 score 积分结算的价值：所在档位价值 + 头像进度。"""
        tier_val = self.tiers[0][1]
        for th, v in self.tiers:
            if score >= th:
                tier_val = v
        return tier_val + self.avatar_eps * max(0, score)

    def tier_of(self, score: int) -> tuple[int | None, int | None]:
        """(当前档位门槛, 下一档门槛)。"""
        cur = nxt = None
        for th, _ in self.tiers:
            if score >= th:
                cur = th
            elif nxt is None:
                nxt = th
        return cur, nxt


@lru_cache(maxsize=16)
def _solver(model: ArenaModel):
    @lru_cache(maxsize=None)
    def value(score: int, hp: int) -> float:
        """从（积分, 血量）继续打下去的期望价值。"""
        if hp <= 0:
            return model.terminal(score)
        if score >= model.max_score:
            return model.terminal(score)
        q = model.win_rate
        ev = 0.0
        for c, pc in model.stake_dist:
            ev += pc * (q * after_win(score, hp, c) + (1 - q) * after_loss(score, hp, c))
        return ev

    def after_win(score: int, hp: int, c: int) -> float:
        s = score + c
        return model.terminal(s) if s >= model.max_score else value(s, hp)

    def after_loss(score: int, hp: int, c: int) -> float:
        if c < hp:
            return value(score, hp - c)
        return model.terminal(score - math.ceil((c - hp) / 2))   # 血量归零：超出部分折半没收，本场结束

    return value, after_win, after_loss


def value(model: ArenaModel, score: int, hp: int) -> float:
    return _solver(model)[0](score, hp)


def win_value(model: ArenaModel, score: int, hp: int, c: int) -> float:
    return _solver(model)[1](score, hp, c)


def loss_value(model: ArenaModel, score: int, hp: int, c: int) -> float:
    return _solver(model)[2](score, hp, c)


@dataclass
class ArenaContext:
    """一局竞技场对局的整场背景（开局读一次，局内不变）。"""
    score: int
    hp: int
    model: ArenaModel = field(default_factory=ArenaModel)

    def breakeven(self, stake_low: int, stake_high: int) -> float:
        """把最终赌注从 stake_low 提到 stake_high（Snap）值得的最低胜率。"""
        m, s, h = self.model, self.score, self.hp
        gain = win_value(m, s, h, stake_high) - win_value(m, s, h, stake_low)
        risk = loss_value(m, s, h, stake_low) - loss_value(m, s, h, stake_high)
        if gain <= 0:
            return 1.0
        return max(m.snap_floor, min(1.0, risk / (gain + risk)))

    def retreat_cutoff(self, stake_now: int, stake_final: int) -> float:
        """撤退线：胜率低于它时撤退（输当前赌注）比打到底（赢/输终局赌注）更好。"""
        m, s, h = self.model, self.score, self.hp
        lose_now = loss_value(m, s, h, stake_now)
        w, l = win_value(m, s, h, stake_final), loss_value(m, s, h, stake_final)
        if w <= l:
            return 1.0
        return max(m.retreat_floor, min(1.0, (lose_now - l) / (w - l)))

    def prompt(self, cube_next: int, final_turn: bool) -> dict:
        """注入逐回合决策提示词的竞技场背景。"""
        no_snap = cube_next if final_turn else cube_next * 2      # 不 Snap 时的终局赌注（终局自动翻倍）
        cur, nxt = self.model.tier_of(self.score)
        return {
            "hp": self.hp, "score": self.score,
            "tier_now": cur, "next_tier_at": nxt, "max_score": self.model.max_score,
            "snap_breakeven": round(100 * self.breakeven(no_snap, no_snap * 2)),
        }


def retreat_check(state, win_prob: float | None, ctx: ArenaContext, threshold: float | None = None) -> tuple[bool, str]:
    """竞技场版撤退判断：时点与排位相同（对手刚 Snap 的回合、最后一回合），胜率线按整场期望算。"""
    if win_prob is None:
        return False, ""
    final_turn = state.turn >= state.max_turn
    opp_snap_now = state.opp_snap_turn is not None and state.opp_snap_turn >= state.turn - 1
    if not (final_turn or opp_snap_now):
        return False, ""
    s = max(1, state.cube_stake)
    nxt = state.cube_next or s
    f = nxt if final_turn else nxt * 2
    cut = threshold if threshold is not None else ctx.retreat_cutoff(s, f)
    why = (f"{'最后一回合' if final_turn else '对手刚 Snap'}：胜率 {win_prob:.0%}，血量 {ctx.hp} 积分 {ctx.score}，"
           f"撤退输 {s}，打下去 赢/输 {f}，整场期望撤退线 {cut:.0%}")
    return win_prob < cut, why


def snap_veto(win_prob: float | None, ctx: ArenaContext, cube_next: int, final_turn: bool) -> tuple[bool, str]:
    """大模型要 Snap 但它给的胜率低于整场期望的 Snap 门槛：否决。"""
    if win_prob is None:
        return False, ""
    no_snap = cube_next if final_turn else cube_next * 2
    be = ctx.breakeven(no_snap, no_snap * 2)
    if win_prob < be:
        return True, f"胜率 {win_prob:.0%} 低于整场 Snap 门槛 {be:.0%}（血量 {ctx.hp}，积分 {ctx.score}）"
    return False, ""

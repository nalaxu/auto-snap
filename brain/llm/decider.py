"""大模型逐回合决策（场地分析与出牌合并为一次调用）。

一次请求完成：逐个场地评估本回合放牌利弊 -> 综合比分、双方手牌/牌库、后续能量做规划 -> 本回合出牌。
- 知识库（data/locations.json）作为「已核实参考」随局面提供；未收录的场地由模型依自身了解判断，
  其评估写入对局日志，赛后复盘再沉淀回知识库
- 结构化输出；结果必须通过 validator，否则在时间预算内带错误反馈重试一次
- 超时 / 出错 / 仍不合法 -> 规则 AI 兜底（不阻塞回合）
- 回合计时：有计时的对局用 effort_timed（默认 low）+ 硬截止 deadline_timed_s（默认 12s）；大模型在后台线程思考，
  到点未返回立即用规则 AI 方案出牌（实测 gpt-6.1-sol：medium 中位 17s，low 中位 10.7s）
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import replace
from typing import Any

from .. import rule_based, strategy
from ..locations import phase
from ..models import ActivateAction, GameState, MoveAction, PlayAction, TurnDecision
from ..validator import activatable, validate
from .base import LLMClient, LLMError, TaskConfig

log = logging.getLogger(__name__)

SYSTEM = """你是《漫威终极逆转》（Marvel Snap 国服）的顶尖玩家，为我方决定本回合的操作。目标：期望方块数最大。

规则：3 个场地，每方每地最多 4 张；通常 6 回合，第 N 回合 N 点能量（效果可改变）。本回合出牌总费用 ≤ energy，
只能打 hand 中的牌；双方同时出牌、回合结束揭示，actions 顺序即出牌（揭示）顺序。终局赢下至少 2 个场地者胜，场地数相同比总战力。

决策要点：
1. 先读 board_notes（关键信息栈，每回合重建）：可激活 / 可移动、放牌顺序、本回合生效的效果、持续限制、对手动向与节奏、
   场地事件。它是本回合最重要的信息。
2. 场地：判断本回合往每个已揭示场地放牌的利弊（随时间收益的早放，负面的晚放），限制必须遵守。locations[].kb 是已核实条目；
   未揭示场地效果未知。
3. 局势与规划：比分、空位、对手剩余手牌与已揭示的威胁、我方后续能量曲线（deck_remaining、energy_by_turn），选定要争的两路。
4. 出牌：用满能量，只为下回合关键 combo 才保留；能量用不满时选能量利用高、能带来翻盘机会的牌。纸面战力不是唯一标准：
   有条件的牌等条件成熟，先打能铺垫后续的牌。已大幅领先的路不再堆战力；最后一回合已稳两路时去争第三路。
   deck_notes（如有）：我方卡组的固定打法与手牌要点（优先遵守，含出牌优先级）、已识别的对手卡组及应对。
5. snap：每回合判断，reason 里写一句理由。只在优势局 Snap，最好在中期、关键牌亮出之前；我方 combo 已打出并开始兑现收益
   就是好的 Snap 点，不要求已经领先两路。stakes_hint 是出牌前的领先情况。最后一回合明显领先时不 Snap（会吓跑对手）；信息不足、只领先一路、对手手牌多能量足时不 Snap；
   对手已 Snap 而我方没有明显优势不跟。can_snap=false 时填 false。
6. activate / moves：激活不花能量、每张一次，最迟最后一回合用掉（除非会输掉某一路），只能激活 activatable 里的牌；
   有可移动时判断移动能否多赢或守住一路（含移动本身触发的收益）。执行顺序：激活 → 移动 → 出牌：
   移走的牌空出的格子本回合可以出牌，满员的地点可以先移走一张再打需要同路配合的牌。
7. win_probability：本回合出牌后我方最终获胜的概率（0–100）。要现实，考虑对手未揭示的手牌与终局爆发；程序据此计算撤退。
8. arena（如有，竞技场局）：整场背景。hp / score 是我方本场血量与积分；snap_breakeven 是程序按血量、积分、
   宝箱档位算出的 Snap 胜率门槛（%），你的 win_probability 达不到它时不要 Snap（程序也会否决）。hand_notes 是手牌的竞技场要点。双方卡组都是随机组的，不要套用排位的固定卡组思路。

只输出 JSON。card 用 hand 中的 id，location 用场地 idx（0/1/2）。文字用中文且简短：effect / why ≤ 20 字，plan、reason ≤ 40 字。"""

_LOC = {"type": "integer", "enum": [0, 1, 2]}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "location_assessment": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": _LOC,
                    "effect": {"type": "string"},
                    "favorable_now": {"type": "integer", "enum": [-3, -2, -1, 0, 1, 2, 3]},
                    "why": {"type": "string"},
                },
                "required": ["location", "effect", "favorable_now", "why"],
                "additionalProperties": False,
            },
        },
        "target_locations": {"type": "array", "items": _LOC},
        "plan": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"card": {"type": "string"}, "location": _LOC},
                "required": ["card", "location"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
        "snap": {"type": "boolean"},
        "win_probability": {"type": "integer", "minimum": 0, "maximum": 100},
        "moves": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"card": {"type": "string"}, "from_location": _LOC, "to_location": _LOC},
                "required": ["card", "from_location", "to_location"],
                "additionalProperties": False,
            },
        },
        "activate": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"card": {"type": "string"}, "location": _LOC},
                "required": ["card", "location"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["location_assessment", "target_locations", "plan", "actions", "reason", "snap", "win_probability",
                 "activate", "moves"],
    "additionalProperties": False,
}


def nothing_to_decide(state: GameState) -> TurnDecision | None:
    """本回合没有任何可做的事 -> 直接结束回合，不问大模型（如 T1 手牌全是 2 费以上）。

    条件：没有费用够且有合法地点的手牌、没有可激活 / 可移动的场上牌；且不需要做 Snap / 撤退判断
    （前两回合不 Snap；最后一回合与对手刚 Snap 的回合要算撤退，照常问模型）。
    """
    from ..location_rules import can_play, move_targets

    if any(c.cost <= state.energy and any(can_play(state, c, l.idx) for l in state.locations) for c in state.hand):
        return None
    if any(activatable(state, l) for l in state.locations):
        return None
    if any(move_targets(state, c, l.idx) for l in state.locations for c in l.my_cards):
        return None
    final_turn = state.turn >= state.max_turn
    opp_snap_now = state.opp_snap_turn is not None and state.opp_snap_turn >= state.turn - 1
    if final_turn or opp_snap_now or (state.can_snap and state.turn > 2):
        return None
    return TurnDecision(reason="没有能出的牌、可激活或可移动的牌，直接结束回合（不调用大模型）", source="skip:no_playable")


def retreat_check(state: GameState, win_prob: float | None, threshold: float | None = None) -> tuple[bool, str]:
    """是否撤退（期望方块最大）。只在两个时点考虑（用户经验）：对手刚 Snap 的回合、最后一回合；其余回合尽量打下去。

    撤退：输掉当前赌注 S。打下去：赢/输终局赌注 F（最后一回合 F = 下回合赌注，已含终局翻倍；更早 F = 下回合赌注 × 2）。
    期望 (2p − 1)·F < −S 时撤退，即 p < (1 − S/F) / 2（F = 2S 时 p < 25%）。threshold 给定则改用固定胜率阈值。
    """
    if win_prob is None:
        return False, ""
    final_turn = state.turn >= state.max_turn
    opp_snap_now = state.opp_snap_turn is not None and state.opp_snap_turn >= state.turn - 1
    if not (final_turn or opp_snap_now):
        return False, ""
    s = max(1, state.cube_stake)
    nxt = state.cube_next or s
    f = nxt if final_turn else nxt * 2
    cut = threshold if threshold is not None else (1 - s / f) / 2
    why = (f"{'最后一回合' if final_turn else '对手刚 Snap'}：胜率 {win_prob:.0%}，撤退输 {s}，"
           f"打下去 赢/输 {f}（期望 {(2 * win_prob - 1) * f:+.1f}），撤退阈值 {cut:.0%}")
    return win_prob < cut, why


def stakes_hint(state: GameState) -> dict:
    """本回合出牌前的领先情况（供 Snap 判断）：每路分差（我方 − 对手）、领先/落后路数、剩余回合。"""
    diffs = [l.my_power - l.opp_power for l in state.locations]
    return {
        "diff_by_location": diffs,
        "leading": sum(1 for d in diffs if d > 0),
        "trailing": sum(1 for d in diffs if d < 0),
        "turns_left_after_this": max(0, state.max_turn - state.turn),
        "unrevealed_locations": sum(1 for l in state.locations if not l.revealed),
    }


def build_prompt(state: GameState, advisor=None, cards_db: dict | None = None) -> str:
    """局面 + 资料库关联：手牌带当前费用/战力与能力；场上双方的牌、牌库、弃牌只给 id，
    其能力统一放在 card_info（同一张牌只出现一次，控制提示长度）；场地附带资料库效果。"""
    advisor = advisor or strategy.default_advisor()
    cards_db = cards_db if cards_db is not None else strategy.cards_db()
    card_info: dict[str, str] = {}

    def note(cid: str) -> str:
        if cid not in card_info:
            meta = cards_db.get(cid, {})
            stats = f"{meta.get('cost', '?')}费{meta.get('power', '?')}力"
            card_info[cid] = f"{stats}；{meta.get('ability') or '无能力'}" if meta else "资料库无此卡"
        return cid

    def hand_card(c):
        meta = cards_db.get(c.id, {})
        return {"id": c.id, "cost": c.cost, "power": c.power, "ability": meta.get("ability", "") if meta else "资料库无此卡"}

    locs = []
    for l in state.locations:
        info = advisor.info(l.name) if l.revealed and l.name else None
        kb = None
        if info and info.known:
            kb = {"effect": info.effect, "bias_now": info.bias_at(state.turn, state.max_turn), "notes": info.notes,
                  "verified": info.source != "db" and "unverified" not in info.tags}
            if info.source == "db":   # 只有资料库效果文字，利弊由模型判断
                kb = {"effect": info.effect, "source": "资料库（未评估利弊）"}
        locs.append({
            "idx": l.idx, "name": l.name or None, "revealed": l.revealed, "kb": kb,
            "my_power": l.my_power, "opp_power": l.opp_power, "my_slots_free": l.my_slots_free,
            "my_cards": [note(c.id) for c in l.my_cards], "opp_cards_revealed": [note(c.id) for c in l.opp_cards],
            "opp_card_count": l.opp_card_count,
        })
    turns_left = max(0, state.max_turn - state.turn)
    data = {
        "board_notes": state.board_notes,
        "turn": state.turn, "max_turn": state.max_turn, "phase": phase(state.turn, state.max_turn), "turns_left_after_this": turns_left,
        "energy": state.energy, "energy_by_turn": {t: t for t in range(state.turn + 1, state.max_turn + 1)},
        "cube_stake": state.cube_stake, "cube_next": state.cube_next,
        "can_snap": state.can_snap, "opp_snapped": state.opp_snapped,
        "stakes_hint": stakes_hint(state),
        "activatable": [{"card": c.id, "location": l.idx, "ability": (cards_db.get(c.id) or {}).get("ability", "")
                         + ("" if "Activate:" in ((cards_db.get(c.id) or {}).get("ability") or "")
                            else "（画面显示可激活：能力是复制 / 场地给来的，具体效果看场上要点或按牌面推断）")}
                        for l in state.locations for c in activatable(state, l)],
        "played_last_turn": state.played_last_turn,
        "hand": [hand_card(c) for c in state.hand],
        "deck_remaining": [note(c) for c in state.deck_remaining],
        "my_deck_count": state.my_deck_count,
        "my_graveyard": [note(c) for c in state.my_graveyard],
        "opp_hand_count": state.opp_hand_count,
        "opp_deck_count": state.opp_deck_count,
        "locations": locs,
        "card_info": card_info,
        "forbidden": [{"card": c, "location": i} for c, i in state.forbidden],
    }
    if state.arena is not None:   # 竞技场：随机卡组，不用排位的卡组 / 对手原型知识库，改注入整场背景与手牌要点
        from ..arena_ratings import default_ratings

        arena = dict(state.arena)
        hand_notes = {c.id: (default_ratings().get(c.id) or {}).get("note") for c in state.hand}
        arena["hand_notes"] = {k: v for k, v in hand_notes.items() if v}
        data["arena"] = arena
    else:
        from .. import decks

        opp_cards = state.opp_seen or [c.id for l in state.locations for c in l.opp_cards]
        notes = decks.default_kb().prompt_notes(state.deck_name, [c.id for c in state.hand], opp_cards)
        if notes:
            data["deck_notes"] = notes
    return "当前局面：\n" + json.dumps(data, ensure_ascii=False)


class LLMDecider:
    """返回 TurnDecision；模型的场地评估 / 规划保存在 last_raw，供对局日志记录。"""

    def __init__(self, client: LLMClient, task: TaskConfig, max_attempts: int = 2,
                 effort_timed: str | None = "low", deadline_s: float = 40.0, deadline_timed_s: float = 12.0,
                 deadline_late_s: float | None = None, late_from_turn: int = 5,
                 timer_reserve_s: float = 7.0, min_llm_s: float = 5.0):
        self.client = client
        self.task = task
        self.max_attempts = max_attempts
        self.effort_timed = effort_timed
        self.deadline_s = deadline_s
        self.deadline_timed_s = deadline_timed_s
        self.deadline_late_s = deadline_late_s if deadline_late_s is not None else deadline_timed_s
        self.late_from_turn = late_from_turn
        self.timer_reserve_s = timer_reserve_s   # 留给出牌 + 结束回合的时间（拖一张约 1-1.5s，结束回合约 1-3s）
        self.min_llm_s = min_llm_s               # 剩余预算低于这个就不问大模型，直接规则 AI
        self.last_raw: dict | None = None
        self.last_latency: float | None = None
        self.last_note = ""
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm")
        self._warm_future = None
        self._warm_checked = 0.0

    def warmup(self) -> None:
        """后台预热/保持到模型服务的连接（等待匹配、对手回合、揭示动画期间调用；内部限频，不阻塞）。"""
        if not hasattr(self.client, "warmup") or time.time() - self._warm_checked < 5:
            return
        self._warm_checked = time.time()
        if self._warm_future is None or self._warm_future.done():
            self._warm_future = self._pool.submit(self.client.warmup)

    def _task_for(self, state: GameState) -> tuple[TaskConfig, float]:
        if state.turn_timer:
            # 后期回合场上牌多、局面复杂，模型更慢；官方回合约 1 分钟（后期略长），可放宽截止
            deadline = self.deadline_late_s if state.turn >= self.late_from_turn else self.deadline_timed_s
            task = replace(self.task, effort=self.effort_timed or self.task.effort,
                           timeout_s=min(self.task.timeout_s, deadline + 5))
            return task, deadline
        return self.task, self.deadline_s

    def time_budget(self, state: GameState) -> float | None:
        """按内存里的回合倒计时算大模型可用时间：剩余 − 读取后已过去的时间 − 执行预留。读不到计时返回 None。"""
        if state.turn_seconds_left is None:
            return None
        left = state.turn_seconds_left
        if state.timer_read_at is not None:
            left -= max(0.0, time.monotonic() - state.timer_read_at)
        return left - self.timer_reserve_s

    def planned_deadline(self, state: GameState) -> float:
        """本回合最多等大模型多少秒（与 decide 的截止逻辑一致）；0 = 不调用大模型（没牌可出 / 时间不够）。供面板显示。"""
        if nothing_to_decide(state) is not None:
            return 0.0
        _, deadline = self._task_for(state)
        budget = self.time_budget(state)
        if budget is not None and budget < self.min_llm_s:
            return 0.0
        return min(deadline, budget) if budget is not None else deadline

    def decide(self, state: GameState) -> TurnDecision:
        self.last_raw, self.last_latency, self.last_note = None, None, ""
        if (skip := nothing_to_decide(state)) is not None:
            self.last_latency, self.last_note = 0.0, skip.reason
            return skip
        task, deadline = self._task_for(state)
        budget = self.time_budget(state)
        if budget is not None and budget < self.min_llm_s:
            # 回合快结束（如上回合没出牌导致本回合时间极短、或很晚才识别到对局）：不等大模型
            self.last_latency, self.last_note = 0.0, f"回合剩余 {state.turn_seconds_left:.0f}s，不够大模型思考"
            d = rule_based.decide(state)
            d.reason = f"[{self.last_note}，规则AI] " + d.reason
            return d
        if budget is not None and budget < deadline:
            deadline = budget
            task = replace(task, timeout_s=min(task.timeout_s, deadline + 5))
        start = time.time()
        future = self._pool.submit(self._ask, state, task, start + deadline)
        try:
            decision, raw = future.result(timeout=deadline)
        except FutureTimeout:
            decision, raw = None, None
            self.last_note = f"大模型 {deadline:.0f}s 内未返回"
            turn = state.turn

            def _late(f, start=start, turn=turn):   # 迟到结果只记日志，供调整截止时间
                try:
                    d, _ = f.result()
                    acts = ", ".join(f"{a.card}→{a.location}" for a in d.actions) if d else "无效"
                    log.info("大模型迟到返回 T%d %.1fs：%s", turn, time.time() - start, acts)
                except Exception as e:  # noqa: BLE001
                    log.info("大模型迟到失败 T%d %.1fs：%r", turn, time.time() - start, e)
            future.add_done_callback(_late)
        except Exception as e:  # noqa: BLE001
            decision, raw = None, None
            self.last_note = f"大模型异常：{e!r}"
        self.last_latency = time.time() - start
        self.last_raw = raw
        if decision is not None:
            return decision
        fallback = rule_based.decide(state)
        fallback.reason = f"[{self.last_note or '大模型失败'}，规则AI兜底] " + fallback.reason
        return fallback

    def _ask(self, state: GameState, task: TaskConfig, deadline_ts: float) -> tuple[TurnDecision | None, dict | None]:
        user = build_prompt(state)
        raw = None
        for attempt in range(self.max_attempts):
            if attempt and time.time() > deadline_ts - 5:
                break   # 剩余时间不够再问一次
            try:
                d = self.client.complete_json(task, SYSTEM, user, SCHEMA, "turn_decision")
            except LLMError as e:
                log.warning("大模型决策失败：%s", e)
                break
            raw = d
            try:
                p = d.get("win_probability")
                decision = TurnDecision(actions=[PlayAction(**a) for a in d["actions"]], reason=d["reason"],
                                        activate=[ActivateAction(**a) for a in d.get("activate") or []],
                                        moves=[MoveAction(**m) for m in d.get("moves") or []],
                                        snap=bool(d.get("snap")) and state.can_snap,
                                        win_probability=p / 100 if isinstance(p, (int, float)) else None,
                                        source=f"llm:{task.model}/{task.effort}")
            except Exception as e:  # noqa: BLE001
                log.warning("大模型输出无法解析：%r", e)
                break
            errors = validate(state, decision)
            if not errors:
                return decision, raw
            log.info("大模型决策不合法：%s", errors)
            user += "\n\n上一次输出不合法：\n- " + "\n- ".join(errors) + "\n请修正后重新输出。"
        return None, raw

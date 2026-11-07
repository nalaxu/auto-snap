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
from ..models import ActivateAction, GameState, PlayAction, TurnDecision
from ..validator import activatable, validate
from .base import LLMClient, LLMError, TaskConfig

log = logging.getLogger(__name__)

SYSTEM = """你是《漫威终极逆转》（Marvel Snap 国服）的顶尖玩家，为我方决定本回合出牌。

规则：
- 3 个场地，每方每个场地最多 4 张牌；通常 6 回合，第 N 回合有 N 点能量（效果可能改变）。
- 本回合打出的牌总费用 ≤ energy；只能打出 hand 中的牌，每张一次；双方同时出牌，回合结束时揭示。
- 终局赢下 3 个场地中至少 2 个者胜；场地数相同比总战力。actions 的顺序即出牌（揭示）顺序。

请按以下步骤综合思考：
0. 先读 board_notes（场上最新要点，每回合重建）：【本回合有效】的跟牌触发（如 Hawkeye）、【持续】效果与限制、
   【对手上回合】新打出的牌、【场地事件】即将发生的回合效果。这些是本回合最需要考虑的信息。
1. 场地评估：逐个分析已揭开的场地，判断「本回合往这里放牌」对我方利好还是不利（结合剩余回合数）。
   - 随时间累积收益的场地（如每回合 +1 战力）：越早放越好，尽量多放。
   - 负面效果的场地（如把我方打出的牌复制给对手、削弱战力、随时间惩罚）：前期避免，最后一回合再放以降低影响。
   - 出牌限制（某回合禁止/强制出牌、某费用禁止）必须遵守。
   - locations[].kb 是已核实的知识库条目（effect/bias/notes），可信度高；kb 为空时依据你对该场地的了解判断，没把握就当中性。
   - 未揭示的场地（revealed=false）效果未知。
   - card_info 是场上双方的牌、我方牌库/弃牌的资料（费用、战力、能力）；注意对手已揭示牌的 Ongoing 限制（如禁止在该场地出高费牌）。
2. 局势：各场地比分、可用空位；决定争夺哪 2 个场地（target_locations）。对手手牌数 opp_hand_count 与对手已揭示的牌
   提示其后续威胁；我方剩余牌库 deck_remaining 与 energy_by_turn 用于规划后续回合（保留关键大牌、凑能量曲线）。
3. 本回合出牌：尽量用满能量（除非刻意留给后续规划），考虑卡牌能力（On Reveal 条件、Ongoing、Medusa 放中间、
   Hawkeye 需要下回合在同一场地跟牌等），已大幅领先的场地不要浪费战力。
   - 能量利用率：浪费能量是重大失误（用户复盘 b5c577ce T5：5 点能量只打 3 费 White Queen）。除非刻意为下回合
     关键 combo 保留，否则必须用满；能量用不满时，优先选「能量利用更高、且带来翻盘机会」的牌
     （如 Loki 把牌库换成对手起始卡组并降费，后续能抽到对手的强牌；落后时尤其重要）。reason 里写明剩余能量及原因。
   - 纸面战力不是唯一标准：有条件的减费/成长牌（如 Firelord 在条件满足时降费）留到条件成熟再打，
     本回合先打能铺垫后续（补手牌、触发 combo）的牌。增益场地（如给低费牌加成的场地）优先放能吃到加成的牌。
   - 最后一回合（turns_left_after_this=0）分路原则：已稳定领先两路时，把本回合资源投向第三路争取三路全优——
     对手必须同时翻两路才能赢，胜率与容错都更高；不要继续往已大幅领先的路堆战力。
     若只领先一路，则优先保住领先最少、最可能被翻的那一路和最有希望拿下的第二路。

4. 是否 Snap（snap 字段，加注）——每回合都必须认真判断，并在 reason 里写明 Snap 或不 Snap 的一句理由。
   方块决定段位：不 Snap 赢一局只得 2 方块，Snap 后赢一局得 4；输了同样翻倍，所以只在优势局 Snap。
   - Snap 后下回合赌注翻倍，终局再自动翻倍；每局只能 Snap 一次，can_snap=false 时必须填 false。
   - stakes_hint 给出本回合出牌前的领先情况（每路分差、领先路数、剩余回合）。按本回合出牌后的局面判断：
     · 我方 combo 打出并实际生效之后就是好的 Snap 点（如减费/生成牌引擎已落地并开始兑现收益、持续成长场地已拉开分差），
       这一条不要求已经领先两路：引擎生效意味着后续每回合都比对手多拿资源，第 3 回合就可以 Snap。
       例：T2 Iron Patriot / Quinjet 等生成或减费牌落地、T3 已兑现（手牌增加、费用降低、场地领先），T3 即 Snap。
       越早 Snap，对手越难判断，也越早锁定翻倍。
     · 应当 Snap：预计领先 2 路且每路领先 ≥ 5（或一路大幅领先 ≥ 10、另一路稳定领先），
       对手已揭示牌里没有明显的翻盘威胁；我方后续手牌/能量曲线更强时也应 Snap。
     · 第 1–2 回合信息太少，一般不 Snap。
     · 目标是期望方块数最大，而不是 Snap 次数。最理想的时机是中期第 3–5 回合，并且在我方 Key 牌（决定胜负的核心牌，
       如 Loki、终局大牌、combo 收尾牌）亮出来之前 Snap：Key 牌揭示后对手就会看清局势而撤退。
       计划下回合打出 Key 牌时，本回合就是 Snap 的好时机。
     · 最后一回合（turns_left_after_this=0）若我方已明显领先、对手看得出自己要输，不要 Snap：Snap 会直接吓跑对手
       （对手撤退我方只得当前赌注，不 Snap 打完终局自动翻倍反而更多）。最后一回合只在局势仍不明朗、
       而我方本回合出牌后能确保翻盘/锁定胜局（对手看不出来）时才考虑 Snap。
     · 不要 Snap：只领先 1 路、分差都很小、关键场地未揭示、对手手牌多且能量高、或我方后续没有关键牌。
   - 对手已 Snap（opp_snapped=true）说明对手有信心：我方没有明显优势时不跟。

5. activate：「激活」（Activate）类场上牌不在打出当回合生效，之后的回合可以主动激活一次触发效果（如 Danger：
   摧毁此处一张战力比它低的敌方牌）。activatable 列出本回合可激活的我方场上牌及其能力；判断现在激活是否有利
   （目标是否存在、时机是否最佳——有的效果留到后面更好），要激活就写进 activate（card + location）。激活先于本回合出牌执行。
6. win_probability：按本回合出牌后的局面，估计我方最终赢下本局的概率（0–100 整数）。要现实：对手已揭示牌的威胁、
   对手剩余手牌数与能量、各路分差与空位、我方剩余回合能补的战力都要考虑。程序据此计算是否撤退（不要自己判断撤退）。
只输出 JSON。card 用 hand 中的 id，location 用场地 idx（0/1/2）。不出牌时 actions 为空数组。
文字字段用中文且尽量简短：每个 effect / why ≤ 20 字，plan 与 reason 各 ≤ 40 字（回合有计时，越快越好）。"""

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
                 "activate"],
    "additionalProperties": False,
}


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
        "activatable": [{"card": c.id, "location": l.idx, "ability": (cards_db.get(c.id) or {}).get("ability", "")}
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

    def decide(self, state: GameState) -> TurnDecision:
        self.last_raw, self.last_latency, self.last_note = None, None, ""
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

"""竞技场场外流程：主界面 → Game Modes → Arena Mode → 门票开场 → 12 轮组卡 → 换牌 → PLAY；
单局结束后（Collect Rewards → Next）回到竞技场界面继续。对局本身复用排位的对局循环。

每一步：截图识别界面（screens.arena_screen）+ 读内存（perception.arena），执行后用内存确认生效
（组卡后卡组 +1、换牌后 DataVersion 变化），不靠固定等待。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from brain import arena_draft
from gamelog.arena_log import ArenaRunLog, game_record
from perception.arena import ArenaState

from . import screens
from .layout import ARENA_BUY_CANCEL, ARENA_CHEST, ARENA_DRAFT_SELECT, ARENA_SWAP_REFRESH, ARENA_SWAP_SELECT, arena_deck_pos

log = logging.getLogger(__name__)


class ArenaStop(RuntimeError):
    """竞技场流程需要停下（没门票且不允许买、未识别的弹窗、整场结算界面未知……），message 给用户看。"""


@dataclass
class ArenaOptions:
    buy_ticket_with_gold: bool = False   # 门票不足时用金块买（150 金块 / 张）
    max_runs: int = 0                    # 打完 N 场竞技后停止（0 = 不限）
    swap_threshold: float = 1.0          # 换牌规则兜底的提升阈值


def _card(cid: str, deck: list[str] | None = None) -> dict:
    """面板卡牌：名字 / 费用 / 战力 / 评分 + 衍生牌（creates）+ 与本场卡组的点名联系（links）。"""
    from brain.arena_links import default_links
    from brain.arena_ratings import default_ratings
    from brain.knowledge import default_db

    db = default_db()
    c = db.card(cid)
    lk = default_links()
    return {"id": cid, "name": c.get("name") or cid, "cost": c.get("cost"), "power": c.get("power"),
            "rating": default_ratings().rating(cid), "creates": lk.token_info(cid),
            "links": [{"id": d, "name": db.card(d).get("name") or d, "dir": k} for d, k in lk.link_pairs(deck or [], cid)]}


def state_payload(st: ArenaState) -> dict:
    """面板用的竞技场状态：数值 + 卡牌（名字 / 费用 / 战力 / 评分 / 衍生牌 / 点名联系）+ 档位进度。
    候选的联系按「换进 / 选进卡组后」算。"""
    from gamelog.arena_stats import TIERS

    nxt = next((t for t in TIERS if t > st.score), None)
    return {**st.summary(), "deck": [_card(c, st.deck) for c in st.deck],
            "candidates": [_card(c, st.deck) for c in st.candidates],
            "max_hp": st.config.max_hp, "max_score": st.config.max_score, "score_per_swap": st.config.score_per_swap,
            "next_tier": nxt, "run_id": st.run_id}


class ArenaFlow:
    def __init__(self, ex, read_arena: Callable[[], ArenaState | None], options: ArenaOptions | None = None,
                 emit: Callable[..., None] | None = None, live: bool = True, decider=None,
                 run_log: ArenaRunLog | None = None):
        """decider：brain.llm.arena_decider.ArenaDecider（大模型组卡 / 换牌）；None 用规则打分。
        emit：面板事件（gamelog.live.LiveBus.emit）；run_log：整场记录 logs/arena/runs.jsonl。"""
        self.ex = ex
        self.decider = decider
        self.read_arena = read_arena
        self.opt = options or ArenaOptions()
        self.emit = emit or (lambda *a, **k: None)
        self.live = live
        self.runs_started = 0
        self.runs_finished: set[str] = set()
        self.last_run_id = ""
        self.log = run_log
        self.prev: ArenaState | None = None          # 上一次读到的状态（对比出一局的积分 / 血量变化）
        self.pending_game: dict | None = None        # 主循环告知的刚结束对局 {game_id, result, cube_value}
        self._last_summary: dict | None = None

    def _event(self, kind: str, run_id: str, **data) -> None:
        """面板事件 arena_<kind> + 整场记录。"""
        self.emit(f"arena_{kind}", run_id=run_id, **data)
        if self.log:
            self.log.write(kind, run_id, **data)

    def note_game(self, game_id: str, result: str | None, cube_value: int | None) -> None:
        """主循环：一局竞技场对局结束（结果来自对局快照），等下次读到整场状态变化时写 game 记录。"""
        self.pending_game = {"game_id": game_id, "result": result, "cube_value": cube_value}

    def observe(self, st: ArenaState | None) -> None:
        """每次读到状态：整场开始 / 结束、单局积分血量变化、面板状态更新。"""
        self._track_run(st)
        if not st:
            return
        p = self.prev
        if p and p.run_id == st.run_id and st.games > p.games:
            g = self.pending_game or {}
            rec = game_record(p, st, g.get("result"), g.get("cube_value"), g.get("game_id", ""))
            self.pending_game = None
            if self.log:
                self.log.write("game", st.run_id, **rec)
            print(f"  竞技场结算：积分 {p.score} → {st.score}（{st.score - p.score:+d}），血量 {p.hp} → {st.hp}"
                  + (f"，超出 {rec['overflow']} 没收 {rec['forfeit']}" if rec.get("forfeit") is not None else ""))
        if p is None or st.run_id != p.run_id or st.games != p.games or st.version != p.version:
            self.prev = st
        summary = st.summary()
        if summary != self._last_summary:
            self._last_summary = summary
            self.emit("arena_state", **state_payload(st))

    # ------------------------------------------------------------ 工具
    def _wait_state(self, cond: Callable[[ArenaState], bool], timeout_s: float = 6.0) -> ArenaState | None:
        end = time.time() + timeout_s
        while time.time() < end:
            st = self.read_arena()
            if st and cond(st):
                return st
            time.sleep(0.3)
        return None

    def _track_run(self, st: ArenaState | None) -> None:
        """记录整场的开始 / 结束（用于 max_runs 与日志）。"""
        if not st or not st.run_id:
            return
        if st.run_id != self.last_run_id and not st.finished:
            self.last_run_id = st.run_id
            print(f"  ⚔ 竞技场：本场 {st.run_id[:8]}（积分 {st.score}，血量 {st.hp}，已打 {st.games} 局）")
        if st.finished and st.run_id not in self.runs_finished and st.run_id == self.last_run_id:
            self.runs_finished.add(st.run_id)
            print(f"  🏁 本场竞技结束：积分 {st.score}，血量 {st.hp}，共 {st.games} 局")
            self._event("run_end", st.run_id, score=st.score, hp=st.hp, games=st.games, deck=st.deck)

    def reached_max_runs(self) -> bool:
        return bool(self.opt.max_runs) and len(self.runs_finished) >= self.opt.max_runs

    # ------------------------------------------------------------ 一步
    def step(self, press_play: bool) -> str:
        """对局外执行一步，返回识别到的界面 / 动作名。press_play=False 时不开新的对局（面板暂停、到达局数）。"""
        img = self.ex.capture()
        if img is None:
            return "no_window"
        name, d = screens.arena_screen(img)
        st = self.read_arena()
        self.observe(st)
        if name is None:
            m = screens.current(img)
            if m and m.name in ("collect_rewards", "next"):     # 单局结算：Collect Rewards → Next
                self.ex.click_rel(m.center, m.name)
                time.sleep(2.5)
                return m.name
            return "unknown"
        if name == "arena_buy":        # 没票点了开始：花 150 金块买票（只在设置允许、且确实要开新一场时确认）
            if self.opt.buy_ticket_with_gold and press_play and not self.reached_max_runs():
                cost = st.config.gold_cost if st else 150
                print(f"  买票：花 {cost} 金块进入竞技场（设置 buy_ticket_with_gold = true）")
                self.ex.click_rel(d["arena_buy_confirm"].center, f"确认花 {cost} 金块买票")
                self.emit("arena_buy", cost=cost)
                if self.log:
                    self.log.write("buy_ticket", (st.run_id if st else "") or "", cost=cost)
                time.sleep(3.0)
                return "arena_buy_confirm"
            self.ex.click_rel(ARENA_BUY_CANCEL, "取消买票")
            time.sleep(1.5)
            if not self.opt.buy_ticket_with_gold:
                raise ArenaStop("竞技场门票为 0，且未允许用金块购买（设置 [arena].buy_ticket_with_gold）")
            return "arena_buy_cancel"
        if name == "arena_chest":      # 整场结束：开宝箱 → CLAIM → 回到竞技场主页
            self.ex.click_rel(ARENA_CHEST, "打开竞技场宝箱")
            time.sleep(3.0)
            return name
        if name == "arena_claim":
            self.ex.click_rel(d["arena_claim"].center, "领取宝箱奖励（CLAIM）")
            time.sleep(3.0)
            return name
        if name == "main_menu":
            if not press_play:
                return name
            self.ex.click_rel(d["game_modes"].center, "Game Modes")
            time.sleep(1.5)
            return name
        if name == "game_modes":
            self.ex.click_rel(d["modes_arena"].center, "Arena Mode")
            time.sleep(2.0)
            return name
        if st is None:
            return "arena_loading"     # 界面已打开但管理器状态还没读到
        if name == "arena_landing":
            return self._landing(st, d, press_play)
        if name == "arena_board":
            if st.phase == "draft":
                return self._draft(st)
            if st.phase == "run":
                if st.swaps_left > 0:
                    return self._swap(st)
                if press_play:
                    self.ex.click_rel(d["arena_play"].center, "竞技场 PLAY")
                    time.sleep(2.0)
                    return "arena_play"
                return "arena_ready"
            return "arena_board"
        # arena_popup：只有标题、没有按钮——可能是买票 / 整场结算等还没标定的界面
        path = self.ex.save_evidence("arena_popup")
        raise ArenaStop(f"出现未识别的竞技场弹窗（截图 {path}），请手动处理并把截图发给开发者")

    def _landing(self, st: ArenaState, d: dict, press_play: bool) -> str:
        if not press_play:
            return "arena_landing"
        if self.reached_max_runs():
            return "arena_landing"
        if st.tickets <= 0 and not self.opt.buy_ticket_with_gold:
            raise ArenaStop("竞技场门票为 0，且未允许用金块购买（设置 [arena].buy_ticket_with_gold）")
        self.ex.click_rel(d["arena_tickets"].center, "Arena Tickets（开始竞技）")
        if st.tickets <= 0:
            return "arena_tickets"     # 会弹出买票确认，下一步 arena_buy 分支处理
        new = self._wait_state(lambda s: s.phase == "draft", timeout_s=8)
        if new:
            self.runs_started += 1
            self._event("run_start", new.run_id, tickets=new.tickets)
            print(f"  ▶ 开始新一场竞技（剩余门票 {new.tickets}）")
        return "arena_start"

    def _draft(self, st: ArenaState) -> str:
        if len(st.candidates) < 3:
            return "arena_draft_wait"
        cands = st.candidates[:3]
        pick = (self.decider.pick(st.deck, cands, st.config.deck_size) if self.decider
                else arena_draft.choose_pick(st.deck, cands, st.config.deck_size))
        print(f"  组卡 {st.draft_round}/{st.config.deck_size}：{cands} → {pick.card}（{pick.source}：{pick.reason}）")
        self._event("pick", st.run_id, round=st.draft_round, candidates=cands, pick=pick.card,
                    scores=[round(x, 2) for x in pick.scores], reason=pick.reason, source=pick.source)
        n = len(st.deck)
        self.ex.click_rel(ARENA_DRAFT_SELECT[pick.index], f"Select {pick.card}")
        if not self.live:
            return "arena_pick"
        new = self._wait_state(lambda s: len(s.deck) > n)
        if not new:
            self.ex.save_evidence(f"arena_pick_unconfirmed_{pick.card}")
            print("  ⚠ 选牌后卡组没有变化（截图已保存），稍后重试")
        elif pick.card not in new.deck:
            print(f"  ⚠ 卡组新增的不是 {pick.card}：{new.deck}")
        return "arena_pick"

    def _swap(self, st: ArenaState) -> str:
        if len(st.candidates) < 3 or len(st.deck) < st.config.deck_size:
            return "arena_swap_wait"
        cands = st.candidates[:3]
        ctx = {"score": st.score, "hp": st.hp, "games": st.games, "swaps_left": st.swaps_left}
        sw = (self.decider.swap(st.deck, cands, ctx) if self.decider
              else arena_draft.choose_swap(st.deck, cands, self.opt.swap_threshold))
        ver = st.version
        if sw.out_index is None:
            print(f"  换牌：候选 {cands} 都不值得换（{sw.source}：{sw.reason}），刷新放弃")
            self._event("swap", st.run_id, candidates=cands, out=None, inc=None, reason=sw.reason, source=sw.source)
            self.ex.click_rel(ARENA_SWAP_REFRESH, "刷新（放弃换牌）")
        else:
            print(f"  换牌：{sw.out_card} → {sw.in_card}（{sw.source}：{sw.reason}）")
            self._event("swap", st.run_id, candidates=cands, out=sw.out_card, inc=sw.in_card, reason=sw.reason,
                        source=sw.source)
            self.ex.press_rel(arena_deck_pos(sw.out_index), f"选中 {sw.out_card}", hold_s=1.0)
            time.sleep(0.5)
            self.ex.click_rel(ARENA_SWAP_SELECT[sw.in_index], f"Select {sw.in_card}")
        if not self.live:
            return "arena_swap"
        new = self._wait_state(lambda s: s.version != ver or s.swaps_left < st.swaps_left)
        if not new:
            self.ex.save_evidence("arena_swap_unconfirmed")
            raise ArenaStop("换牌后内存没有变化（截图已保存）：换牌操作可能没生效，请检查后重新启动")
        # 卡组按费用排序（界面顺序 = 内存顺序），换进来的牌不一定在原位置：按张数核对
        if sw.out_index is not None and (new.deck.count(sw.out_card) >= st.deck.count(sw.out_card)
                                         or new.deck.count(sw.in_card) <= st.deck.count(sw.in_card)):
            self.ex.save_evidence("arena_swap_mismatch")
            print(f"  ⚠ 换牌结果与预期不同（想换下 {sw.out_card}、换进 {sw.in_card}）：{new.deck}")
        return "arena_swap"

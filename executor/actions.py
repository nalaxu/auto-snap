"""执行层：把决策动作变成鼠标操作，并用内存快照确认每个动作已生效。

- play(card_id, location)：按 CardDefId 在**当前**手牌中定位（每打出一张，手牌会重排）
  -> 拖到地点我方一侧 -> 确认该卡实体出现在目标地点；失败则换落点重试一次
- end_turn()：点击结束回合 -> 确认 EndedTurn == Turn
- 安全：动作前确保游戏窗口在前台；任何时候按 F8 中止；dry_run 只打印不动鼠标
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import mouse
from .layout import END_TURN_BUTTON, LOCATION_DROP, RETREAT_BUTTON, SNAP_BUTTON, Rect, board_slot_pos, hand_card_pos
from .window import WindowNotFound, client_rect, find_window, is_foreground, wait_foreground

log = logging.getLogger(__name__)

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "logs" / "evidence"
DROP_Y_FALLBACK = 0.66   # 首次落点没生效时的备用落点（更靠下，我方卡牌区内）


class ActionFailed(RuntimeError):
    pass


class GameOver(ActionFailed):
    """操作前发现对局已结束（如对手撤退）：本回合不再操作。"""


def _live(s: dict | None) -> dict:
    if not s:
        raise ActionFailed("读取不到对局状态")
    if s.get("finished"):
        raise GameOver("对局已结束" + ("（对手撤退）" if (s.get("opp") or {}).get("conceded") else ""))
    return s


class PlayRejected(ActionFailed):
    """拖拽后卡牌仍在手牌：多半是地点规则不允许（上层应记为禁止组合并重新决策）。"""

    def __init__(self, card_id: str, location: int):
        super().__init__(f"{card_id} 无法打到地点{location}（被游戏拒绝，可能是地点限制）")
        self.card_id, self.location = card_id, location


class Executor:
    def __init__(self, snapshot: Callable[[], dict | None], process_name="SnapCN.exe",
                 dry_run: bool = False, verify_timeout_s: float = 2.5):
        mouse.enable_dpi_awareness()
        self.snapshot = snapshot
        self.process_name = process_name
        self.dry_run = dry_run
        self.verify_timeout_s = verify_timeout_s
        self.hwnd = 0
        self.rect: Rect | None = None

    # ------------------------------------------------------------ 窗口
    def _locate(self) -> None:
        try:
            self.hwnd = find_window(self.process_name)
        except WindowNotFound as e:
            raise ActionFailed(str(e)) from e

    def prepare(self) -> Rect:
        """每回合开始调用：重新定位窗口（可能被移动/缩放/最小化）并切到前台。"""
        self._locate()
        if not self.dry_run and not wait_foreground(self.hwnd):
            raise ActionFailed("游戏窗口未能切到前台")
        time.sleep(0.1)
        self.rect = client_rect(self.hwnd)      # 切到前台（可能刚从最小化还原）后再取尺寸
        if self.rect.width < 400:
            raise ActionFailed(f"游戏窗口尺寸异常：{self.rect}")
        return self.rect

    def _guard(self) -> None:
        mouse.check_abort()
        if not self.dry_run and not is_foreground(self.hwnd):
            raise ActionFailed("游戏窗口失去焦点，停止操作")

    def _wait(self, cond: Callable[[dict], bool]) -> dict | None:
        end = time.time() + self.verify_timeout_s
        while time.time() < end:
            mouse.check_abort()
            s = self.snapshot()
            if s and s.get("finished"):
                _live(s)          # 对手撤退等：立即中止，不再等确认
            if s and cond(s):
                return s
            time.sleep(0.1)
        return None

    def save_evidence(self, tag: str) -> Path | None:
        """失败现场截图 -> logs/evidence/。截图失败不影响主流程。"""
        try:
            from .capture import capture_window

            assert self.rect is not None
            img = capture_window(self.hwnd, self.rect.width, self.rect.height)
            if img is None:
                return None
            out = EVIDENCE_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{tag}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            log.info("已保存现场截图 %s", out)
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("现场截图失败：%r", e)
            return None

    # ------------------------------------------------------------ 界面
    def capture(self):
        from .capture import capture_window

        self._locate()
        self.rect = client_rect(self.hwnd)
        if self.rect.width < 400:   # 最小化中：实操模式下还原窗口再截图（演练模式不动窗口）
            if self.dry_run or not wait_foreground(self.hwnd):
                return None
            time.sleep(0.5)
            self.rect = client_rect(self.hwnd)
            if self.rect.width < 400:
                return None
        return capture_window(self.hwnd, self.rect.width, self.rect.height)

    def screen(self):
        """识别当前界面按钮 -> screens.Match 或 None。"""
        from . import screens

        img = self.capture()
        return screens.current(img) if img is not None else None

    def overlay_active(self) -> bool:
        from . import screens

        try:
            img = self.capture()
        except Exception:  # noqa: BLE001
            return False
        return img is not None and screens.overlay_active(img)

    def click_rel(self, rel: tuple[float, float], what: str = "") -> None:
        assert self.rect is not None
        pos = self.rect.to_px(*rel)
        log.info("点击 %s %s", what, pos)
        if self.dry_run:
            return
        was_fg = is_foreground(self.hwnd)
        if not wait_foreground(self.hwnd):
            raise ActionFailed("游戏窗口未能切到前台")
        # 刚切到前台时 Unity 可能吞掉第一次点击：多等一会
        time.sleep(0.1 if was_fg else 0.6)
        mouse.click(pos)

    # ------------------------------------------------------------ 动作
    def play(self, card_id: str, location: int) -> None:
        s = _live(self.snapshot())
        hand = s["me"]["hand"]
        idx = next((i for i, c in enumerate(hand) if c.get("id") == card_id), None)
        if idx is None:
            raise ActionFailed(f"手牌中没有 {card_id}：{[c.get('id') for c in hand]}")
        entity = hand[idx]["entity"]
        assert self.rect is not None
        src = self.rect.to_px(*hand_card_pos(idx, len(hand)))
        drops = [LOCATION_DROP[location], (LOCATION_DROP[location][0], DROP_Y_FALLBACK)]

        for attempt, drop in enumerate(drops, 1):
            dst = self.rect.to_px(*drop)
            log.info("出牌 %s（手牌[%d]/%d）-> 地点%d  %s -> %s  第%d次", card_id, idx, len(hand), location, src, dst, attempt)
            if self.dry_run:
                return
            self._guard()
            mouse.drag(src, dst)
            ok = self._wait(lambda s: any(c["entity"] == entity for c in s["locations"][location]["my_cards"]))
            if ok:
                return
            # 若卡牌落到了别的地点，不再重试（避免越错越多）
            s2 = self.snapshot() or {}
            wrong = [l["idx"] for l in s2.get("locations", []) if any(c["entity"] == entity for c in l["my_cards"])]
            if wrong:
                raise ActionFailed(f"{card_id} 落到了地点{wrong[0]}，而不是地点{location}")
            time.sleep(0.3)
        s3 = self.snapshot() or {}
        _live(s3 or None)   # 拖拽期间对局结束（对手撤退）：不算地点限制
        if any(c.get("entity") == entity for c in s3.get("me", {}).get("hand", [])):
            self.save_evidence(f"rejected_T{s3.get('turn')}_{card_id}_loc{location}")
            raise PlayRejected(card_id, location)
        raise ActionFailed(f"{card_id} 拖到地点{location} 未生效")

    def snap(self) -> None:
        """点击右侧魔方加注，用内存确认（我方 CanRaiseStakes 变 False 或下回合赌注翻倍）。"""
        s = _live(self.snapshot())
        if s["me"].get("can_raise_stakes") is False:
            raise ActionFailed("本局已经 Snap 过")
        before = s.get("cube_value_next") or s.get("cube_value") or 1
        assert self.rect is not None
        pos = self.rect.to_px(*SNAP_BUTTON)
        log.info("Snap T%s 赌注 %s  点击 %s", s.get("turn"), before, pos)
        if self.dry_run:
            return
        for _ in range(2):
            self._guard()
            mouse.click(pos)
            if self._wait(lambda s2: s2["me"].get("can_raise_stakes") is False or (s2.get("cube_value_next") or 0) > before):
                return
            time.sleep(0.3)
        self.save_evidence(f"snap_failed_T{s.get('turn')}")
        raise ActionFailed("点击魔方后赌注没有变化")

    def activate(self, card_id: str, location: int) -> None:
        """长按我方场上牌触发激活（Activate）能力，用内存 Activated 标志确认。"""
        s = _live(self.snapshot())
        mine = s["locations"][location]["my_cards"]
        i, card = next(((i, c) for i, c in enumerate(mine) if c.get("id") == card_id), (None, None))
        if card is None:
            raise ActionFailed(f"地点{location} 没有 {card_id}：{[c.get('id') for c in mine]}")
        slot = card["pos"] if card.get("pos") in (0, 1, 2, 3) else i   # ZonePosition = 格子（与列表顺序不同）
        before = s["me"].get("activations") or 0
        assert self.rect is not None
        pos = self.rect.to_px(*board_slot_pos(location, slot))
        log.info("激活 %s（地点%d 第%d格）长按 %s，已激活次数 %s", card_id, location, slot, pos, before)
        if self.dry_run:
            return
        from . import screens

        def staged() -> bool:
            """画面出现「Press & Hold to Deactivate」= 已激活（待揭示）；内存计数要到揭示才变，作补充判断。"""
            end = time.time() + 1.5
            while time.time() < end:
                img = self.capture()
                if img is not None and screens.activation_staged(img) >= screens.THRESHOLD:
                    return True
                s2 = self.snapshot()
                if s2 and (s2["me"].get("activations") or 0) > before:
                    return True
                time.sleep(0.2)
            return False

        for hold in (1.5, 2.2):   # 只有确认「未激活」才再按：已激活时再长按会取消激活
            self._guard()
            mouse.press(pos, hold_s=hold)
            if staged():
                return
        self.save_evidence(f"activate_failed_T{s.get('turn')}_{card_id}")
        raise ActionFailed(f"长按 {card_id} 后画面没有出现激活状态")

    def move(self, card_id: str, from_loc: int, to_loc: int) -> None:
        """把我方场上牌从一个地点拖到另一个地点（场地允许移入 / 卡牌 Moveable），用内存确认它出现在目标地点。"""
        s = _live(self.snapshot())
        mine = s["locations"][from_loc]["my_cards"]
        i, card = next(((i, c) for i, c in enumerate(mine) if c.get("id") == card_id), (None, None))
        if card is None:
            raise ActionFailed(f"地点{from_loc} 没有 {card_id}：{[c.get('id') for c in mine]}")
        entity = card["entity"]
        slot = card["pos"] if card.get("pos") in (0, 1, 2, 3) else i
        assert self.rect is not None
        src = self.rect.to_px(*board_slot_pos(from_loc, slot))
        dst = self.rect.to_px(*LOCATION_DROP[to_loc])
        log.info("移动 %s 地点%d(第%d格) -> 地点%d  %s -> %s", card_id, from_loc, slot, to_loc, src, dst)
        if self.dry_run:
            return
        for drop in (dst, self.rect.to_px(LOCATION_DROP[to_loc][0], DROP_Y_FALLBACK)):
            self._guard()
            mouse.drag(src, drop)
            if self._wait(lambda s2: any(c.get("entity") == entity for c in s2["locations"][to_loc]["my_cards"])):
                return
        self.save_evidence(f"move_failed_T{s.get('turn')}_{card_id}")
        raise ActionFailed(f"{card_id} 没能移到地点{to_loc}")

    def retreat(self) -> None:
        """点左下 RETREAT，用内存确认（我方 Conceded 或对局结束）。

        确认弹窗的样子尚未实测：不猜点击位置。未确认时保存截图、按 Esc 关闭可能的弹窗，抛 ActionFailed（上层照常出牌）。
        """
        s = _live(self.snapshot())
        assert self.rect is not None
        pos = self.rect.to_px(*RETREAT_BUTTON)
        log.info("撤退 T%s 赌注 %s  点击 %s", s.get("turn"), s.get("cube_value"), pos)
        if self.dry_run:
            return
        self._guard()
        mouse.click(pos)
        end = time.time() + 3
        while time.time() < end:
            s2 = self.snapshot()
            if s2 and (s2.get("finished") or s2["me"].get("conceded")):
                return
            time.sleep(0.1)
        self.save_evidence(f"retreat_dialog_T{s.get('turn')}")
        mouse.key("esc")
        time.sleep(0.5)
        raise ActionFailed("点击 RETREAT 后未确认撤退（截图已保存，待补确认弹窗模板）")

    def end_turn(self) -> None:
        s = _live(self.snapshot())
        turn = s["turn"]
        assert self.rect is not None
        pos = self.rect.to_px(*END_TURN_BUTTON)
        log.info("结束回合 T%s  点击 %s", turn, pos)
        if self.dry_run:
            return
        for _ in range(2):
            self._guard()
            mouse.click(pos)
            if self._wait(lambda s: s.get("turn") != turn or (s["me"].get("ended_turn") or 0) >= turn or s.get("finished")):
                return
        raise ActionFailed("点击结束回合未生效")

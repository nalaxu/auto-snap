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
from .layout import END_TURN_BUTTON, LOCATION_DROP, Rect, hand_card_pos
from .window import WindowNotFound, client_rect, find_window, is_foreground, wait_foreground

log = logging.getLogger(__name__)

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "logs" / "evidence"
DROP_Y_FALLBACK = 0.66   # 首次落点没生效时的备用落点（更靠下，我方卡牌区内）


class ActionFailed(RuntimeError):
    pass


class PlayRejected(ActionFailed):
    """拖拽后卡牌仍在手牌：多半是地点规则不允许（上层应记为禁止组合并重新决策）。"""

    def __init__(self, card_id: str, location: int):
        super().__init__(f"{card_id} 无法打到地点{location}（被游戏拒绝，可能是地点限制）")
        self.card_id, self.location = card_id, location


class Executor:
    def __init__(self, snapshot: Callable[[], dict | None], process_name: str = "SnapCN.exe",
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
        if not wait_foreground(self.hwnd):
            raise ActionFailed("游戏窗口未能切到前台")
        time.sleep(0.1)
        mouse.click(pos)

    # ------------------------------------------------------------ 动作
    def play(self, card_id: str, location: int) -> None:
        s = self.snapshot()
        if not s:
            raise ActionFailed("读取不到对局状态")
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
        if any(c.get("entity") == entity for c in s3.get("me", {}).get("hand", [])):
            self.save_evidence(f"rejected_T{s3.get('turn')}_{card_id}_loc{location}")
            raise PlayRejected(card_id, location)
        raise ActionFailed(f"{card_id} 拖到地点{location} 未生效")

    def end_turn(self) -> None:
        s = self.snapshot()
        if not s:
            raise ActionFailed("读取不到对局状态")
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

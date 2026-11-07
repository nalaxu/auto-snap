"""执行层：坐标标定、出牌定位与生效确认（mouse 被替换，不产生真实输入）。"""
import pytest

from executor import actions, mouse
from executor.layout import Rect, hand_card_pos

W, H = 2000, 1125  # 标定截图尺寸


@pytest.mark.parametrize("n,expected_x", [
    (5, [700, 848, 993, 1138, 1285]),   # T4 截图
    (4, [773, 920, 1065, 1212]),        # T5/T6 截图
])
def test_hand_positions_match_screenshots(n, expected_x):
    xs = [hand_card_pos(i, n)[0] * W for i in range(n)]
    assert all(abs(a - b) < 8 for a, b in zip(xs, expected_x)), xs


def _snap(hand, loc_cards=((), (), ()), turn=3, ended=2):
    return {
        "game_id": "g", "turn": turn, "finished": False,
        "me": {"hand": [{"id": c, "entity": e} for c, e in hand], "ended_turn": ended},
        "locations": [{"idx": i, "my_cards": [{"entity": e} for e in cards]} for i, cards in enumerate(loc_cards)],
    }


class FakeGame:
    """模拟游戏：drag 落到地点区域则把卡从手牌移到该地点。"""

    def __init__(self, rect, hand):
        self.rect = rect
        self.hand = list(hand)
        self.locs = [[], [], []]
        self.drags = []
        self.turn, self.ended = 3, 2
        self.ignore_first_drag = False

    def snapshot(self):
        return _snap(self.hand, self.locs, self.turn, self.ended)

    def drag(self, src, dst):
        self.drags.append((src, dst))
        if self.ignore_first_drag and len(self.drags) == 1:
            return
        n = len(self.hand)
        idx = min(range(n), key=lambda i: abs(self.rect.to_px(*hand_card_pos(i, n))[0] - src[0]))
        loc = min(range(3), key=lambda i: abs(self.rect.to_px(0.37 + 0.13 * i, 0.6)[0] - dst[0]))
        self.locs[loc].append(self.hand.pop(idx)[1])

    def click(self, pos):
        self.ended = self.turn


@pytest.fixture
def game(monkeypatch):
    rect = Rect(11, 103, 2537, 1427)
    g = FakeGame(rect, [("Hulk", 1), ("Medusa", 2), ("Hawkeye", 3)])
    monkeypatch.setattr(mouse, "drag", g.drag)
    monkeypatch.setattr(mouse, "click", g.click)
    monkeypatch.setattr(mouse, "check_abort", lambda: None)
    ex = actions.Executor(g.snapshot, verify_timeout_s=0.5)
    ex.rect, ex.hwnd = rect, 1
    monkeypatch.setattr(actions, "is_foreground", lambda h: True)
    return g, ex


def test_play_uses_current_hand_index_and_verifies(game):
    g, ex = game
    ex.play("Medusa", 2)
    ex.play("Hawkeye", 0)          # 手牌已重排：Hawkeye 现在是 [1]/2
    assert g.locs == [[3], [], [2]]
    assert g.drags[1][0] == ex.rect.to_px(*hand_card_pos(1, 2))
    ex.end_turn()
    assert g.ended == g.turn


def test_play_retries_with_fallback_drop(game):
    g, ex = game
    g.ignore_first_drag = True
    ex.play("Hulk", 1)
    assert len(g.drags) == 2 and g.locs[1] == [1]
    assert g.drags[0][1] != g.drags[1][1]  # 第二次换了落点


def test_play_missing_card_raises(game):
    _, ex = game
    with pytest.raises(actions.ActionFailed):
        ex.play("Thor", 0)


def test_play_rejected_when_card_stays_in_hand(game, monkeypatch):
    g, ex = game
    monkeypatch.setattr(mouse, "drag", lambda s, d: g.drags.append((s, d)))  # 游戏拒绝：牌留在手里
    with pytest.raises(actions.PlayRejected) as ei:
        ex.play("Hulk", 0)
    assert (ei.value.card_id, ei.value.location) == ("Hulk", 0)

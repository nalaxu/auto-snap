"""界面识别回归：用户提供的对局截图（放大到实机客户区 2537×1427 再识别）。"""
from pathlib import Path

import pytest
from PIL import Image

from executor import screens

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fname,expected,center", [
    ("ingame_t4.jpg", "end_turn", (0.91, 0.91)),
    ("match_end.jpg", "collect_rewards", (0.91, 0.92)),
    ("battle_rewards.jpg", "next", (0.91, 0.92)),
    ("main_menu.jpg", "play", (0.50, 0.85)),
])
def test_detect_screens(fname, expected, center):
    img = Image.open(FIX / fname).resize((2537, 1427))
    m = screens.current(img)
    assert m is not None and m.name == expected
    assert abs(m.center[0] - center[0]) < 0.02 and abs(m.center[1] - center[1]) < 0.02
    others = [x.score for n, x in screens.detect(img).items() if n != expected]
    assert max(others) < screens.THRESHOLD


@pytest.mark.parametrize("fname,expected", [
    ("guide_snap_overlay.jpg", True),     # 实机：「Tap to SNAP!」指引遮罩，拖牌被屏蔽
    ("ingame_t4.jpg", False),
    ("match_end.jpg", False),
    ("battle_rewards.jpg", False),
    ("main_menu.jpg", False),
])
def test_guide_overlay_detection(fname, expected):
    img = Image.open(FIX / fname).resize((2537, 1427))
    assert screens.overlay_active(img) is expected


@pytest.mark.parametrize("fname,expected", [
    ("never_seen.jpg", "never_seen"),            # 对手新卡全屏展示（点任意位置跳过）
    ("guide_snap_overlay.jpg", "tap_to_snap"),   # 「Tap to SNAP!」指引（须点魔方）
])
def test_known_overlays_are_recognized(fname, expected):
    img = Image.open(FIX / fname).resize((2537, 1427))
    m = screens.current(img)
    assert m is not None and m.name == expected
    assert expected in screens.OVERLAY_CLICKS


def test_snap_click_matches_layout():
    from executor.layout import SNAP_BUTTON

    assert screens.OVERLAY_CLICKS["tap_to_snap"] == SNAP_BUTTON


def test_activation_staged_hint():
    """激活后牌上方出现「Press & Hold to Deactivate」（实测 820618e1 T6 Spider-Man）；其他界面不误判。"""
    from PIL import Image

    assert screens.activation_staged(Image.open(FIX / "activate_staged.png")) >= screens.THRESHOLD
    for name in ("ingame_t4.jpg", "main_menu.jpg", "match_end.jpg"):
        assert screens.activation_staged(Image.open(FIX / name)) < 0.5


def test_activate_hint_maps_to_iron_lad_slot():
    """7c965b5e T6：中路右下格的 Iron Lad 复制到激活能力，画面出现「Press & Hold to Activate」。"""
    from PIL import Image

    from executor.layout import hint_to_slot

    hints = screens.activate_hints(Image.open(FIX / "activate_hint_ironlad.png"))
    assert len(hints) == 1
    assert hint_to_slot(hints[0], [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (1, 3), (2, 0)]) == (1, 3)
    for name in ("ingame_t4.jpg", "activate_staged.png", "main_menu.jpg"):
        assert screens.activate_hints(Image.open(FIX / name)) == []

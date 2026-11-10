"""竞技场：界面识别、状态推导、组卡 / 换牌占位决策、场外流程（假执行器 + 截图）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from brain import arena_draft
from executor import screens
from executor.arena_flow import ArenaFlow, ArenaOptions, ArenaStop
from executor.layout import (ARENA_DRAFT_SELECT, ARENA_SWAP_REFRESH, ARENA_SWAP_REFRESH_CONFIRM, ARENA_SWAP_SELECT,
                             arena_deck_pos)
from perception.arena import ArenaConfig, ArenaState

FIX = Path(__file__).parent / "fixtures" / "arena"
DECK = ["Kingpin", "MariaHill", "Scorpion", "EzekielSims", "JeffTheBabyDolphin", "Shanna", "Brood",
        "DraxAvatarOfLife", "Isca", "Wiccan", "SpiderMan2099", "Ultron"]


def img(name: str) -> Image.Image:
    p = FIX / f"{name}.webp"
    return Image.open(p if p.exists() else FIX / f"{name}.png")


@pytest.mark.parametrize("fixture,expected", [
    ("main_menu", "main_menu"), ("game_modes", "game_modes"), ("landing", "arena_landing"),
    ("draft_round1", "arena_board"), ("run_swap0", "arena_board"), ("run_swap1", "arena_board"),
    ("run_after_swap", "arena_board"), ("ingame_t1", None), ("match_end", None), ("victory", None),
    ("matchmaking", None), ("chest_tiers_a", None),
])
def test_arena_screen(fixture, expected):
    name, _ = screens.arena_screen(img(fixture))
    assert name == expected


def test_single_game_result_screens_still_detected():
    assert screens.current(img("match_end")).name == "collect_rewards"
    assert screens.current(img("victory")).name == "next"


def test_state_phase_and_swap_points():
    s = ArenaState(run_id="r", finished=False, deck=DECK[:5])
    assert s.phase == "draft" and s.draft_round == 6
    s.deck = list(DECK)
    assert s.phase == "run"
    # 赢 2 魔方 + 掉 1 血 = 2 + 2 = 4 点（满 4 点换一次，用户给的例子）
    s.score_mark, s.score, s.hp_mark, s.hp = 0, 2, 10, 9
    assert s.swap_points == 4
    assert ArenaState().phase == "none"
    assert ArenaState(run_id="r", finished=True, deck=list(DECK)).phase == "none"


def test_choose_pick_prefers_curve_late():
    # 卡组已经有 3 张 1 费（到上限），后期再给 1 费牌应被曲线压低
    deck = ["Scorpion", "Kingpin", "MariaHill", "EzekielSims", "JeffTheBabyDolphin", "Shanna",
            "Brood", "Isca", "Wiccan", "SpiderMan2099"]
    pick = arena_draft.choose_pick(deck, ["Nightcrawler", "Ultron", "Magneto"], 12)
    assert pick.card != "Nightcrawler"
    assert len(pick.scores) == 3


def test_choose_swap_keeps_or_swaps_by_threshold():
    sw = arena_draft.choose_swap(DECK, ["Leader", "Magneto", "Shocker"], threshold=999)
    assert sw.out_index is None
    sw = arena_draft.choose_swap(DECK, ["Leader", "Magneto", "Shocker"], threshold=-999)
    assert sw.out_index is not None and sw.in_card in ("Leader", "Magneto", "Shocker")


# ---------------------------------------------------------------- 场外流程
class FakeEx:
    def __init__(self, screen: str):
        self.screen = screen
        self.clicks: list[tuple] = []
        self.presses: list[tuple] = []

    def capture(self):
        return img(self.screen)

    def click_rel(self, rel, what=""):
        self.clicks.append((round(rel[0], 3), round(rel[1], 3), what))

    def press_rel(self, rel, what="", hold_s=1.0):
        self.presses.append((round(rel[0], 3), round(rel[1], 3), what))

    def save_evidence(self, tag):
        return None


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import executor.arena_flow as af
    monkeypatch.setattr(af.time, "sleep", lambda s: None)


def flow(screen: str, state: ArenaState | None, **opt) -> tuple[ArenaFlow, FakeEx]:
    ex = FakeEx(screen)
    return ArenaFlow(ex, lambda: state, ArenaOptions(**opt), live=False), ex


def test_main_menu_opens_game_modes_then_arena():
    f, ex = flow("main_menu", None)
    assert f.step(press_play=True) == "main_menu"
    assert ex.clicks and ex.clicks[0][2] == "Game Modes"
    f, ex = flow("game_modes", None)
    f.step(press_play=True)
    assert ex.clicks[0][2] == "Arena Mode"


def test_main_menu_not_clicked_when_paused():
    f, ex = flow("main_menu", None)
    f.step(press_play=False)
    assert not ex.clicks


def test_landing_without_tickets_stops_unless_buying_allowed():
    st = ArenaState(tickets=0)
    f, ex = flow("landing", st)
    with pytest.raises(ArenaStop):
        f.step(press_play=True)
    assert not ex.clicks
    f, ex = flow("landing", st, buy_ticket_with_gold=True)
    f.step(press_play=True)
    assert "Arena Tickets" in ex.clicks[0][2]


def test_landing_with_ticket_starts_run():
    f, ex = flow("landing", ArenaState(tickets=1))
    f.step(press_play=True)
    assert "Arena Tickets" in ex.clicks[0][2]


def test_draft_clicks_select_of_chosen_card():
    st = ArenaState(run_id="r", finished=False, deck=[], candidates=["MaryJane", "Kingpin", "HumanTorch"])
    f, ex = flow("draft_round1", st)
    assert f.step(press_play=True) == "arena_pick"
    pick = arena_draft.choose_pick([], st.candidates, 12)
    x, y = ARENA_DRAFT_SELECT[pick.index]
    assert ex.clicks == [(round(x, 3), round(y, 3), f"Select {pick.card}")]


def test_run_with_swap_presses_deck_card_then_select():
    st = ArenaState(run_id="r", finished=False, deck=list(DECK), candidates=["Leader", "Magneto", "Shocker"],
                    swaps_left=1)
    f, ex = flow("run_swap1", st, swap_threshold=-999)
    assert f.step(press_play=True) == "arena_swap"
    sw = arena_draft.choose_swap(DECK, st.candidates, -999)
    px, py = arena_deck_pos(sw.out_index)
    sx, sy = ARENA_SWAP_SELECT[sw.in_index]
    assert ex.presses == [(round(px, 3), round(py, 3), f"选中 {sw.out_card}")]
    assert ex.clicks == [(round(sx, 3), round(sy, 3), f"Select {sw.in_card}")]


def test_run_swap_refresh_when_not_worth():
    st = ArenaState(run_id="r", finished=False, deck=list(DECK), candidates=["Leader", "Magneto", "Shocker"],
                    swaps_left=1)
    f, ex = flow("run_swap1", st, swap_threshold=999)
    f.step(press_play=True)
    assert ex.clicks == [(round(ARENA_SWAP_REFRESH[0], 3), round(ARENA_SWAP_REFRESH[1], 3), "刷新（放弃换牌）"),
                         (round(ARENA_SWAP_REFRESH_CONFIRM[0], 3), round(ARENA_SWAP_REFRESH_CONFIRM[1], 3), "确认放弃换牌")]


def test_run_without_swaps_presses_play_only_when_allowed():
    st = ArenaState(run_id="r", finished=False, deck=list(DECK), candidates=["Leader", "Magneto", "Shocker"])
    f, ex = flow("run_after_swap", st)
    assert f.step(press_play=False) == "arena_ready" and not ex.clicks
    assert f.step(press_play=True) == "arena_play"
    assert ex.clicks[0][2] == "竞技场 PLAY"


def test_single_game_rewards_clicked_not_esc_close():
    f, ex = flow("victory", ArenaState(run_id="r", finished=False, deck=list(DECK)))
    assert f.step(press_play=True) == "next"


def test_max_runs_counts_finished_runs():
    st = ArenaState(run_id="r1", finished=False, deck=list(DECK), config=ArenaConfig())
    f, _ = flow("run_after_swap", st, max_runs=1)
    f.step(press_play=False)
    st.finished = True
    f._track_run(st)
    assert f.reached_max_runs()


def test_run_end_chest_then_claim():
    assert screens.arena_screen(img("run_end_chest"))[0] == "arena_chest"
    assert screens.arena_screen(img("run_end_rewards"))[0] == "arena_claim"
    f, ex = flow("run_end_chest", ArenaState(run_id="r", finished=True, deck=list(DECK)))
    assert f.step(press_play=False) == "arena_chest" and ex.clicks[0][2] == "打开竞技场宝箱"
    f, ex = flow("run_end_rewards", ArenaState(run_id="r", finished=True, deck=list(DECK)))
    assert f.step(press_play=False) == "arena_claim" and "CLAIM" in ex.clicks[0][2]


def test_buy_ticket_dialog():
    assert screens.arena_screen(img("buy_ticket"))[0] == "arena_buy"
    st = ArenaState(tickets=0)
    f, ex = flow("buy_ticket", st, buy_ticket_with_gold=True)
    assert f.step(press_play=True) == "arena_buy_confirm" and "150 金块" in ex.clicks[0][2]
    f, ex = flow("buy_ticket", st, buy_ticket_with_gold=True)
    assert f.step(press_play=False) == "arena_buy_cancel" and ex.clicks[0][2] == "取消买票"
    f, ex = flow("buy_ticket", st, buy_ticket_with_gold=False)
    with pytest.raises(ArenaStop):
        f.step(press_play=True)
    assert ex.clicks[0][2] == "取消买票"

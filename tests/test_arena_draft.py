"""竞技场 M2：评分表、规则打分（曲线 / 配合）、大模型组卡与换牌（假客户端）。"""
from __future__ import annotations

import pytest

from brain import arena_draft as rules
from brain.arena_ratings import Ratings, card_pool, set_default_ratings
from brain.llm.arena_decider import ArenaDecider
from brain.llm.base import LLMError, TaskConfig

DECK = ["Kingpin", "MariaHill", "Scorpion", "EzekielSims", "JeffTheBabyDolphin", "AuntMay", "Shanna", "Brood",
        "DraxAvatarOfLife", "Wiccan", "SpiderMan2099", "Magneto"]


@pytest.fixture()
def ratings(tmp_path):
    r = Ratings(tmp_path / "ratings.json")
    r.put_many({
        "Leader": {"rating": 8.5, "tags": ["standalone"], "note": "", "source": "auto"},
        "Shocker": {"rating": 4.0, "tags": ["standalone"], "note": "", "source": "auto"},
        "Magneto": {"rating": 8.5, "tags": ["standalone"], "note": "", "source": "auto"},
        "Kingpin": {"rating": 4.5, "tags": ["needs:move"], "note": "", "source": "auto"},
        "JeffTheBabyDolphin": {"rating": 6.0, "tags": ["enabler:move"], "note": "", "source": "auto"},
        "Nightcrawler": {"rating": 5.0, "tags": ["enabler:move"], "note": "", "source": "manual"},
    })
    set_default_ratings(r)
    yield r
    set_default_ratings(None)


def test_put_many_keeps_manual(ratings):
    n = ratings.put_many({"Nightcrawler": {"rating": 1, "tags": [], "note": "x", "source": "auto"}})
    assert n == 0 and ratings.rating("Nightcrawler") == 5.0
    ratings.put_many({"Nightcrawler": {"rating": 1, "tags": [], "note": "x", "source": "auto"}}, overwrite_manual=True)
    assert ratings.rating("Nightcrawler") == 1.0


def test_card_pool_excludes_tokens():
    pool = card_pool()
    assert "Magneto" in pool and "Broodling" not in pool and "Drone" not in pool


def test_card_value_uses_rating_then_stats(ratings):
    assert rules.card_value("Leader") == 8.5
    assert 1 <= rules.card_value("Hulk") <= 10      # 没有评分：按身材估算


def test_synergy_needs_enabler(ratings):
    assert rules.synergy([], "Kingpin") == 0
    assert rules.synergy(["JeffTheBabyDolphin"], "Kingpin") == pytest.approx(rules.SYNERGY_NEEDS)
    assert rules.synergy(["Kingpin"], "JeffTheBabyDolphin") == pytest.approx(rules.SYNERGY_ENABLER)


def test_curve_penalty_and_need():
    assert rules.curve_penalty(["Scorpion"] * 4) < 0       # 1 费 4 张超上限，且其他费用缺口
    assert rules.curve_need(["Scorpion"] * 3, "Kingpin") < 0
    assert rules.curve_need([], "MariaHill") == 1.5


def test_choose_pick_rating_first(ratings):
    p = rules.choose_pick([], ["Shocker", "Leader", "Kingpin"], 12)
    assert p.card == "Leader"


def test_choose_swap_improves_deck(ratings):
    sw = rules.choose_swap(DECK, ["Leader", "Shocker", "Shocker"], threshold=0.5)
    assert sw.in_card == "Leader" and sw.gain > 0.5
    assert rules.choose_swap(DECK, ["Shocker"], threshold=50).out_index is None


# ---------------------------------------------------------------- 大模型（假客户端）
class FakeClient:
    provider = "fake"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete_json(self, task, system, user, schema, schema_name):
        self.calls.append((schema_name, user))
        if self.error:
            raise self.error
        return self.reply


TASK = TaskConfig(model="fake-model")


def test_llm_pick_and_prompt_contains_rating(ratings):
    c = FakeClient({"pick": 2, "reason": "补曲线"})
    p = ArenaDecider(c, TASK).pick(["Leader"], ["Shocker", "Magneto", "Kingpin"])
    assert p.card == "Kingpin" and p.source == "llm:fake-model" and len(p.scores) == 3
    name, user = c.calls[0]
    assert name == "arena_pick" and '"rating": 8.5' in user and '"round": "2/12"' in user


def test_llm_pick_falls_back_on_error(ratings):
    p = ArenaDecider(FakeClient(error=LLMError("boom")), TASK).pick([], ["Shocker", "Leader", "Kingpin"])
    assert p.card == "Leader" and p.source == "rule" and "大模型失败" in p.reason


def test_llm_swap_decline_and_accept(ratings):
    d = ArenaDecider(FakeClient({"swap": False, "out": 0, "in": 0, "reason": "都不如现有"}), TASK)
    assert d.swap(DECK, ["Leader", "Shocker", "Shocker"]).out_index is None
    d = ArenaDecider(FakeClient({"swap": True, "out": 0, "in": 0, "reason": "换强卡"}), TASK)
    sw = d.swap(DECK, ["Leader", "Shocker", "Shocker"], {"hp": 6, "score": 4})
    assert (sw.out_card, sw.in_card) == ("Kingpin", "Leader")


def test_llm_swap_out_of_range_falls_back(ratings):
    d = ArenaDecider(FakeClient({"swap": True, "out": 11, "in": 2, "reason": ""}), TASK)
    sw = d.swap(DECK[:5], ["Leader", "Shocker"])
    assert sw.source == "rule"


# ---- 点名联系 / 衍生牌（brain/arena_links.py）----

def test_links_tokens_and_reach():
    from brain.arena_links import default_links
    lk = default_links()
    assert lk.tokens("SamWilson") == ["CapsShield"]
    assert lk.tokens("Thor") == ["Mjolnir"]
    assert lk.tokens("MaryJane") == [] and lk.reach("MaryJane") == {"SpiderMan"}   # 卡池牌是点名，不算衍生
    assert lk.linked("SpiderMan", "MaryJane")
    assert lk.linked("SquirrelGirl", "SquirrelGirl") is False
    info = lk.token_info("SamWilson")[0]
    assert info["name"] == "Cap's Shield" and "moves" in info["ability"]


def test_links_name_prefers_pool_card():
    from brain.arena_links import default_links
    lk = default_links()
    assert lk.names["Sentinel"] == "Sentinel" and lk.names["Spider-Man"] == "SpiderMan"


def test_synergy_counts_named_link(ratings):
    from brain.arena_links import CardLinks, set_default_links
    lk = CardLinks()
    lk.reach = lambda cid: {"Kingpin"} if cid == "Shocker" else set()     # 假设 Shocker 点名 Kingpin
    set_default_links(lk)
    try:
        assert rules.synergy(["Kingpin"], "Shocker") == pytest.approx(rules.SYNERGY_LINK)
        assert rules.synergy(["Shocker"], "Kingpin") == pytest.approx(rules.SYNERGY_LINK + 0)
    finally:
        set_default_links(None)


def test_swap_prompt_shows_creates_and_links(ratings):
    from brain.llm.arena_decider import card_info
    info = card_info("SamWilson", ["SamWilson", "Magneto"])
    assert info["creates"][0]["name"] == "Cap's Shield"
    assert "links" not in info

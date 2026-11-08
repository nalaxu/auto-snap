"""国服卡牌库：模板反推数值、来源优先级、与国际服对比，以及资料合并（人工 > 国服 > 国际服）。"""
from __future__ import annotations

import json

from brain.knowledge import GameDB, merge_card_dbs
from perception.memory.carddefs import CardDefRow, is_flavor, is_placeholder, keywords, match_params, render
from tools.build_cn_card_db import build_items, diff_global

ZCM_TPL = "<b>End of Turn:</b> Destroy your other cards here to get +{card.AddedPower} Power for each. Move to a location you're losing."
ZCM_RENDERED = "<b>End of Turn:</b> Destroy your other cards here to get +1 Power for each. Move to a location you’re losing."


def test_match_params_from_rendered_text():
    # 渲染文本里的弯引号、前缀杂字符（IL2CPP 字符串长度字段）都不影响匹配
    assert match_params(ZCM_TPL, ["m" + ZCM_RENDERED, "unrelated <b>On Reveal:</b> text"]) == {"card.AddedPower": "1"}
    assert match_params(ZCM_TPL, ["<b>On Reveal:</b> nothing"]) is None
    assert match_params("<b>On Reveal:</b> Draw a card.", []) == {}


def test_match_params_multiple_and_names():
    goose = "<b>Ongoing:</b> Nobody can play cards that cost {card.TargetCardCost_1}, {card.TargetCardCost_2}, or {card.TargetCardCost_3} here."
    got = match_params(goose, ["<b>Ongoing:</b> Nobody can play cards that cost 4, 5, or 6 here."])
    assert got == {"card.TargetCardCost_1": "4", "card.TargetCardCost_2": "5", "card.TargetCardCost_3": "6"}
    tok = "<b>On Reveal:</b> Add a {card.Card_Token.Name} to your hand."
    assert match_params(tok, ["<b>On Reveal:</b> Add a Widow's Bite to your hand."]) == {"card.Card_Token.Name": "Widow's Bite"}


def test_render_and_keywords():
    assert render("+{card.AddedPower} Power", {"card.AddedPower": "3"}) == "+3 Power"
    assert render("+{card.AddedPower} Power", None) == "+? Power"
    assert keywords("<b>On Reveal:</b> x. <b>Activate:</b> y") == ["Activate", "On Reveal"]


def test_flavor_and_placeholder_detection():
    assert is_flavor('<i>"HULK SMASH!"</i>')
    assert not is_flavor("<b>On Reveal:</b> x")
    assert is_placeholder("<color=#ff2c2c>!</color>none")
    assert not is_placeholder("<b>Ongoing:</b> none of your cards")


def _rows():
    return {
        "ZombieCaptainMarvel": CardDefRow("ZombieCaptainMarvel", "Zombie Captain Marvel", ZCM_TPL, 4, 5, ["EndOfTurnTrigger"]),
        "Gamora": CardDefRow("Gamora", "Gamora", "<b>On Reveal:</b> If your opponent played a card here this turn, +{card.AddedPower} Power.", 5, 7),
        "Ares": CardDefRow("Ares", "Ares", "<b>On Reveal:</b> Something new +{card.AddedPower} Power.", 5, 13),
        "Hulk": CardDefRow("Hulk", "Hulk", '<i>"HULK SMASH!"</i>', 6, 14),
        "Ghost": CardDefRow("Ghost", "Ghost", "<color=#ff2c2c>!</color>none", 0, 0),
    }


GLOBAL = {
    "ZombieCaptainMarvel": {"name": "Zombie Captain Marvel", "cost": 4, "power": 6, "keywords": ["Activate"],
                            "ability": "Activate: Destroy your other cards here to get +3 Power for each. Move to a location you're losing."},
    "Gamora": {"name": "Gamora", "cost": 5, "power": 7, "ability": "On Reveal: If your opponent played a card here this turn, +5 Power."},
    "Ares": {"name": "Ares", "cost": 5, "power": 7, "ability": "On Reveal: Compare decks. +6 Power."},
    "Hulk": {"name": "Hulk", "cost": 6, "power": 12, "ability": ""},
    "Ghost": {"name": "Ghost", "cost": 3, "power": 2, "ability": "On Reveal: Old text."},
}


def test_build_items_sources_and_diff():
    items, stats = build_items(_rows(), {ZCM_RENDERED}, GLOBAL)
    zcm = items["ZombieCaptainMarvel"]
    assert zcm["ability"].startswith("End of Turn: Destroy your other cards here to get +1 Power")
    assert zcm["params_source"] == "client" and zcm["keywords"] == ["End of Turn"] and zcm["type"] == "special"
    assert items["Gamora"]["params_source"] == "global" and "+5 Power" in items["Gamora"]["ability"]
    assert items["Ares"]["params_source"] == "unknown" and "+? Power" in items["Ares"]["ability"]
    assert items["Hulk"]["ability"] == "" and items["Hulk"]["type"] == "vanilla" and "HULK" in items["Hulk"]["flavor"]
    assert "Ghost" not in items   # 占位定义不收录
    assert stats == {"client": 1, "global": 1, "unknown": 1, "plain": 1, "skipped": 1}

    diff = {d["id"]: d["changes"] for d in diff_global(items, GLOBAL)}
    assert diff["ZombieCaptainMarvel"]["power"] == [6, 5]
    assert "ability" in diff["ZombieCaptainMarvel"]
    assert diff["Hulk"] == {"power": [12, 14]}
    assert "Gamora" not in diff                 # 数值取自国际服、文字一致：不算差异
    assert "ability" not in diff["Ares"]        # 数值未知时不比较文字，避免误报


def test_merge_priority(tmp_path):
    merged = merge_card_dbs({"A": {"cost": 1, "power": 1, "ability": "x", "series": "S1"}, "B": {"cost": 2, "power": 2}},
                            {"A": {"cost": 3, "power": 4, "ability": "y"}})
    assert merged["A"] == {"cost": 3, "power": 4, "ability": "y", "series": "S1", "source": "cn"}
    assert merged["B"] == {"cost": 2, "power": 2}

    (tmp_path / "card_db.json").write_text(json.dumps({"items": GLOBAL}), "utf-8")
    items, _ = build_items(_rows(), {ZCM_RENDERED}, GLOBAL)
    (tmp_path / "card_db_cn.json").write_text(json.dumps({"items": items}), "utf-8")
    (tmp_path / "cards.json").write_text(json.dumps({"Hulk": {"power": 15, "notes": "人工"}}), "utf-8")
    db = GameDB(tmp_path / "card_db.json", tmp_path / "loc.json", tmp_path / "cards.json")
    assert db.card("ZombieCaptainMarvel")["power"] == 5
    assert db.ability("ZombieCaptainMarvel").startswith("End of Turn")
    assert db.card("Hulk")["power"] == 15 and db.card("Hulk")["type"] == "vanilla"   # 人工 > 国服
    assert db.card("Ghost")["power"] == 2                                              # 国服没有 -> 国际服


def test_kb_cn_diff_and_card_detail(tmp_path):
    from ui.server.kb import KnowledgeBase

    d = tmp_path / "data"
    d.mkdir()
    (d / "card_db.json").write_text(json.dumps({"items": GLOBAL}), "utf-8")
    items, stats = build_items(_rows(), {ZCM_RENDERED}, GLOBAL)
    diff = diff_global(items, GLOBAL)
    (d / "card_db_cn.json").write_text(json.dumps({"_meta": {"client_version": "48.18", "params": stats}, "items": items, "diff": diff}), "utf-8")
    kb = KnowledgeBase(d, tmp_path / "bk")
    r = kb.cn_diff()
    assert r["meta"]["client_version"] == "48.18"
    assert {x["id"] for x in r["diff"]} == {"ZombieCaptainMarvel", "Hulk", "Ares"}
    det = kb.card("ZombieCaptainMarvel")
    assert det["db"]["power"] == 5 and det["db"]["source"] == "cn" and det["global"]["power"] == 6
    assert kb.card("Ghost")["global"] is None
    assert any(c["id"] == "ZombieCaptainMarvel" and c["source"] == "cn" for c in kb.search_cards("zombie"))

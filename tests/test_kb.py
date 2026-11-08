"""知识库管理（ui/server/kb.py）、安全写入（brain/kbfile.py），以及机器人赛后写入不覆盖面板修改。"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi import HTTPException

from brain.kbfile import read_json, update_json
from ui.server.kb import KnowledgeBase, pending_signal


def _dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


@pytest.fixture()
def kb(tmp_path):
    d = tmp_path / "data"
    _dump(d / "card_db.json", {"_meta": {}, "items": {
        "Shuri": {"name": "Shuri", "cost": 4, "power": 3, "ability": "On Reveal: Double the Power of the next card you play here."},
        "Wong": {"name": "Wong", "cost": 4, "power": 2, "ability": "Ongoing: Your On Reveal abilities here happen twice."},
        "Nakia": {"name": "Nakia", "cost": 3, "power": 2, "ability": "On Reveal: Give cards in your hand +1 Power."},
        "Beast": {"name": "Beast", "cost": 2, "power": 2, "ability": "On Reveal: Return your other cards here to your hand."},
    }})
    _dump(d / "location_db.json", {"_meta": {}, "items": {
        "KunLun": {"name": "K'un-Lun", "effect": "When a card moves here, give it +2 Power."},
        "OttosLab": {"name": "Otto's Lab", "effect": "The next card you play here pulls a card..."},
    }})
    _dump(d / "signals.json", {"locations": {"OttosLab": {"scope": "here", "polarity": "good", "note": "x", "source": "auto"}},
                               "cards": {"Shuri": {"scope": "global_here", "polarity": "good", "note": "加倍", "source": "复盘"}}})
    _dump(d / "decks" / "mine.json", {"loki": {"name": "Loki", "plan": "旧打法", "cards": {"Wong": "旧要点"}, "lessons": ["L1"]}})
    _dump(d / "decks" / "opponents.json", {"zombie": {"name": "丧尸", "core": "Wong", "signature": ["Wong", "Nakia"], "plan": "p",
                                                      "counter": "c", "samples": [], "seen_cards": {}}})
    (d / "decks" / "unmatched.jsonl").write_text(
        json.dumps({"game_id": "g1", "date": "2026-11-08", "result": "loss", "cards": ["Nakia", "Beast"]}) + "\n"
        + json.dumps({"game_id": "g2", "date": "2026-11-08", "result": "win", "cards": ["Beast"]}) + "\n", "utf-8")
    _dump(d / "cards.json", {"_schema": "doc", "Wong": {"type": "ongoing", "power": 3}})
    _dump(d / "locations.json", {"_schema": "doc"})
    _dump(d / "unknown_locations.json", {"KunLun": {"seen": 3, "last_seen": "t"}, "OttosLab": {"seen": 1}})
    return KnowledgeBase(d, tmp_path / "backup")


def test_pending_signal():
    assert pending_signal({"source": "auto"})
    assert not pending_signal({"source": "auto（复核：改为 bad）"})
    assert not pending_signal({"source": "auto（已复核 2026-11-08）"})
    assert not pending_signal({"source": "复盘 773d5c5d"})


def test_overview(kb):
    o = kb.overview()
    assert o["counts"] == {"signals": 1, "unmatched": 2, "locations": 2}
    assert o["pending_signals"][0]["id"] == "OttosLab" and o["pending_signals"][0]["name"] == "Otto's Lab"
    assert o["unmatched"][0]["game_id"] == "g2"   # 新的在前
    g1 = o["unmatched"][1]
    assert g1["suggest"][0]["id"] == "zombie" and g1["card_names"]["Nakia"] == "Nakia"
    assert o["unknown_locations"][0] == {"id": "KunLun", "name": "K'un-Lun", "effect": "When a card moves here, give it +2 Power.",
                                         "seen": 3, "last_seen": "t"}


def test_signals_crud_and_backup(kb):
    kb.approve_signal("locations", "OttosLab")
    assert "已复核" in read_json(kb.p_signals)["locations"]["OttosLab"]["source"]
    kb.put_signal("cards", "Beast", {"scope": "global", "polarity": "bad", "note": "  弹回  "})
    assert read_json(kb.p_signals)["cards"]["Beast"]["note"] == "弹回"
    kb.delete_signal("cards", "Shuri")
    assert "Shuri" not in read_json(kb.p_signals)["cards"]
    assert len(list(kb.backup_dir.glob("signals.json.*"))) == 3
    with pytest.raises(HTTPException):
        kb.put_signal("cards", "Beast", {"scope": "everywhere", "polarity": "bad"})
    with pytest.raises(HTTPException):
        kb.put_signal("cards", "../x", {"scope": "here", "polarity": "bad"})
    with pytest.raises(HTTPException):
        kb.delete_signal("cards", "Nobody")


def test_unmatched_assign_create_dismiss(kb):
    kb.assign_unmatched("g1", "zombie")
    z = read_json(kb.p_opp)["zombie"]
    assert z["samples"][0]["game_id"] == "g1" and z["seen_cards"] == {"Nakia": 1, "Beast": 1}
    assert [r["game_id"] for r in kb.unmatched()] == ["g2"]
    kb.put_opponent("bounce", {"name": "弹回", "core": "Beast", "signature": ["Beast"], "plan": "p", "counter": "c"},
                    create=True, from_game="g2")
    b = read_json(kb.p_opp)["bounce"]
    assert b["core"] == "Beast" and b["samples"][0]["result"] == "win" and kb.unmatched() == []
    with pytest.raises(HTTPException) as e:
        kb.put_opponent("bounce", {"name": "x"}, create=True)
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        kb.dismiss_unmatched("g2")


def test_edit_opponent_keeps_samples(kb):
    kb.assign_unmatched("g1", "zombie")
    kb.put_opponent("zombie", {"name": "丧尸2", "core": "Wong", "signature": ["Wong"], "plan": "新", "counter": "新应对"})
    z = read_json(kb.p_opp)["zombie"]
    assert z["name"] == "丧尸2" and len(z["samples"]) == 1 and z["seen_cards"]["Nakia"] == 1
    kb.delete_opponent("zombie")
    assert "zombie" not in read_json(kb.p_opp)


def test_mine(kb):
    kb.put_mine("loki", {"name": "Loki", "plan": "新打法", "cards": {"Wong": "新要点", "Shuri": " ", "Nakia": "n"}, "lessons": ["L1", "L2"]})
    m = read_json(kb.p_mine)["loki"]
    assert m["plan"] == "新打法" and m["cards"] == {"Wong": "新要点", "Nakia": "n"} and m["lessons"] == ["L1", "L2"]
    assert kb.decks()["mine"][0]["card_names"]["Wong"] == "Wong"


def test_cards(kb):
    assert [c["id"] for c in kb.search_cards("double")] == ["Shuri"]
    assert [c["id"] for c in kb.search_cards("", only_curated=True)] == ["Wong"]
    assert kb.search_cards("wong")[0]["power"] == 3   # 人工覆盖优先
    kb.put_card("Shuri", {"cost": "3", "notes": "国服 3 费"})
    assert read_json(kb.p_cards)["Shuri"] == {"cost": 3, "notes": "国服 3 费"}
    kb.put_card("Wong", {"power": 4})
    assert read_json(kb.p_cards)["Wong"] == {"type": "ongoing", "power": 4}   # 保留 type
    with pytest.raises(HTTPException):
        kb.put_card("Shuri", {})
    with pytest.raises(HTTPException):
        kb.put_card("Nobody", {"cost": 1})
    kb.delete_card("Shuri")
    assert "Shuri" not in read_json(kb.p_cards) and "_schema" in read_json(kb.p_cards)


def test_locations(kb):
    kb.put_location("KunLun", {"effect": "移动来 +2", "bias": {"early": 1, "mid": 2, "final": -1}, "notes": "配合 Spider-Man", "tags": ["move"]})
    loc = read_json(kb.p_locs)["KunLun"]
    assert loc["bias"] == {"early": 1, "mid": 2, "final": -1} and loc["source"].startswith("面板编辑")
    assert [x["id"] for x in kb.unknown_locations()] == ["OttosLab"]   # 收录后离开待复核
    assert kb.search_locations("", only_curated=True)[0]["bias"]["mid"] == 2
    with pytest.raises(HTTPException):
        kb.put_location("KunLun", {"bias": {"early": 5}})
    assert kb.location("KunLun")["curated"]["notes"] == "配合 Spider-Man"
    kb.delete_location("KunLun")
    assert "KunLun" not in read_json(kb.p_locs)


def test_update_json_concurrent(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}", "utf-8")

    def bump(i):
        for _ in range(20):
            update_json(p, lambda d: d.__setitem__(str(i), d.get(str(i), 0) + 1))

    ts = [threading.Thread(target=bump, args=(i,)) for i in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert read_json(p) == {str(i): 20 for i in range(5)}
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob("*.lock"))


def test_bot_writes_keep_panel_edits(kb):
    """机器人赛后追加样本时，在磁盘最新版本上改，不会用启动时的旧副本覆盖面板刚做的修改。"""
    from brain.decks import DeckKB
    from brain.signals import SignalKB

    deck_kb = DeckKB(kb.p_opp.parent)                     # 机器人启动时读入
    kb.put_opponent("zombie", {"name": "面板改名", "core": "Wong", "signature": ["Wong", "Nakia"], "plan": "p2", "counter": "c2"})
    kb.put_opponent("new1", {"name": "面板新建", "signature": ["Beast"]}, create=True)
    assert deck_kb.record_game("abcdef123456", ["Wong", "Nakia"], "win") == "zombie"
    opp = read_json(kb.p_opp)
    assert opp["zombie"]["name"] == "面板改名" and opp["zombie"]["samples"][0]["game_id"] == "abcdef12" and "new1" in opp

    sig_kb = SignalKB(kb.p_signals)
    kb.put_signal("cards", "Wong", {"scope": "here", "polarity": "good", "note": "面板加的"})
    sig_kb.get("cards", "Beast", "On Reveal: The next card you play here switches sides.")   # 自动识别并写入
    data = read_json(kb.p_signals)
    assert data["cards"]["Wong"]["note"] == "面板加的" and data["cards"]["Beast"]["source"] == "auto"


def test_api(kb, tmp_path):
    from fastapi.testclient import TestClient

    from ui.server.app import create_app

    with TestClient(create_app(data_dir=kb.data)) as c:
        assert c.get("/api/kb/overview").json()["counts"]["unmatched"] == 2
        assert c.put("/api/kb/signals/cards/Beast", json={"scope": "here", "polarity": "bad", "note": "n"}).json() == {"ok": True}
        assert c.put("/api/kb/signals/cards/Beast", json={"scope": "x", "polarity": "bad"}).status_code == 400
        assert c.post("/api/kb/unmatched/g1/assign", json={"archetype": "zombie"}).status_code == 200
        assert c.post("/api/kb/opponents/b2", json={"name": "B", "signature": ["Beast"], "from_game": "g2"}).status_code == 200
        assert c.get("/api/kb/unmatched").json() == []
        assert c.get("/api/kb/cards", params={"q": "shuri"}).json()[0]["id"] == "Shuri"
        assert c.put("/api/kb/locations/KunLun", json={"effect": "e", "bias": {"early": 1, "mid": 0, "final": 0}}).status_code == 200
        assert c.get("/api/kb/locations/KunLun").json()["curated"]["bias"]["early"] == 1
        assert c.delete("/api/kb/cards/Nobody").status_code == 404

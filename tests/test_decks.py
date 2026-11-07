import json

from brain.decks import DeckKB


def _kb(tmp_path):
    (tmp_path / "mine.json").write_text(json.dumps({"loki": {"plan": "生成牌", "cards": {"Quinjet": "T1 优先", "Loki": "落后时打"}}}), "utf-8")
    (tmp_path / "opponents.json").write_text(json.dumps({"zg": {"name": "丧尸吞心", "core": "ZombieGalacti",
                                                                "signature": ["ZombieGalacti", "Wong", "Nakia", "Shuri"],
                                                                "plan": "终局变身", "counter": "别大 Snap"}}), "utf-8")
    return DeckKB(tmp_path)


def test_prompt_notes_load_only_what_is_relevant(tmp_path):
    kb = _kb(tmp_path)
    n = kb.prompt_notes("loki", ["Quinjet", "Mirage"], ["Shocker"])
    assert n["my_deck_plan"] == "生成牌" and n["hand_tips"] == {"Quinjet": "T1 优先"} and "opponent_deck" not in n
    assert kb.match_opponent(["Wong"]) == []                       # 只有 1 张标志牌：不认定
    assert kb.match_opponent(["Wong", "Nakia"]) == ["zg"]          # 2 张标志牌
    assert kb.match_opponent(["ZombieGalacti"]) == ["zg"]          # 看到核心牌
    assert kb.prompt_notes("other", [], ["ZombieGalacti"])["opponent_deck"][0]["archetype"] == "丧尸吞心"


def test_record_game_samples_and_unmatched(tmp_path):
    kb = _kb(tmp_path)
    assert kb.record_game("a" * 36, ["Wong", "Nakia", "Blade"], "win") == "zg"
    data = json.loads((tmp_path / "opponents.json").read_text("utf-8"))
    assert data["zg"]["samples"][0]["cards"] == ["Blade", "Nakia", "Wong"] and data["zg"]["seen_cards"]["Blade"] == 1
    assert kb.record_game("b" * 36, ["Squirrel", "Hulk"], "loss") is None
    assert "Squirrel" in (tmp_path / "unmatched.jsonl").read_text("utf-8")


def test_temporary_deck_copy_uses_original(tmp_path):
    kb = _kb(tmp_path)
    assert kb.my_deck("loki-tmp") is kb.my_deck("LOKI") is not None
    assert kb.my_deck("aggro") is None

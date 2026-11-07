"""读取当前选中卡组：PlayState.json(SelectedDeckId) + CollectionState.json(ServerState.Decks)。

游戏只在登录/结算时写这些文件，卡组在一局内不变，对局开始时读一次即可。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_STATES_DIR = Path(os.environ.get("USERPROFILE", "~")) / "AppData/LocalLow/NetEase/SnapCN/Standalone/States/prod"


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8-sig"))


def _index(o, byid: dict) -> None:
    if isinstance(o, dict):
        if "$id" in o:
            byid[o["$id"]] = o
        for v in o.values():
            _index(v, byid)
    elif isinstance(o, list):
        for v in o:
            _index(v, byid)


def load_current_deck(states_dir: Path = DEFAULT_STATES_DIR) -> tuple[str, list[str]]:
    """-> (卡组名, [CardDefId × 12])；找不到返回 ("", [])。"""
    try:
        play = _load(states_dir / "PlayState.json")
        coll = _load(states_dir / "CollectionState.json")
    except (OSError, json.JSONDecodeError):
        return "", []
    sel = play.get("SelectedDeckId")
    deck_id = sel.get("Value") if isinstance(sel, dict) else sel
    byid: dict = {}
    _index(coll, byid)
    decks = (coll.get("ServerState") or {}).get("Decks") or []
    decks = [byid.get(d["$ref"], d) if isinstance(d, dict) and "$ref" in d else d for d in decks]
    deck = next((d for d in decks if d.get("Id") == deck_id), decks[0] if len(decks) == 1 else None)
    if not deck:
        return "", []
    cards = [byid.get(c["$ref"], c) if "$ref" in c else c for c in deck.get("Cards") or []]
    return deck.get("Name", ""), [c["CardDefId"] for c in cards if c.get("CardDefId")]

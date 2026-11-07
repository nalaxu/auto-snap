"""卡组知识库：我方卡组打法 + 对手卡组原型（data/decks/）。按需加载，只把当前用得上的几行注入提示词。

- mine.json：按卡组名索引 {name, plan, cards{card: tip}, lessons[]}；提示里只给整体打法 + 当前手牌的要点
- opponents.json：按原型 id 索引 {name, core, signature[], plan, counter, samples[], seen_cards{}}；
  加载时建「卡牌 -> 原型」哈希索引，局中按对手已揭示的牌识别原型（看到核心牌，或 ≥ 2 张标志牌），只注入命中的原型
- 赛后 record_game：命中原型 -> 追加样本、累计见过的牌；未命中 -> 写入 unmatched.jsonl 候选，人工复盘时再决定是否建原型
  （不是所有对手卡组都值得记录）
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

DECKS_DIR = Path(__file__).resolve().parent.parent / "data" / "decks"
MAX_SAMPLES = 30
MATCH_MIN_SIGNATURE = 2


class DeckKB:
    def __init__(self, root: Path = DECKS_DIR):
        self.root = root
        self._lock = threading.Lock()
        self.mine: dict = self._load("mine.json")
        self.opponents: dict = self._load("opponents.json")
        self._index()

    def _load(self, name: str) -> dict:
        try:
            return json.loads((self.root / name).read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _index(self) -> None:
        self.by_card: dict[str, set[str]] = {}
        for aid, a in self.opponents.items():
            for c in {a.get("core"), *a.get("signature", [])} - {None}:
                self.by_card.setdefault(c, set()).add(aid)

    # ------------------------------------------------------------ 查询
    def match_opponent(self, cards) -> list[str]:
        """对手已揭示的牌 -> 命中的原型 id（看到核心牌，或 ≥ 2 张标志牌）。"""
        hits: dict[str, int] = {}
        core_seen: set[str] = set()
        for c in set(cards):
            for aid in self.by_card.get(c, ()):
                hits[aid] = hits.get(aid, 0) + 1
                if self.opponents[aid].get("core") == c:
                    core_seen.add(aid)
        return sorted((a for a, n in hits.items() if a in core_seen or n >= MATCH_MIN_SIGNATURE),
                      key=lambda a: -hits[a])

    def my_deck(self, deck_name: str) -> dict | None:
        """按卡组名找我方卡组资料：大小写不敏感；「原名-后缀」的临时副本（如 LOKI-TMP）沿用原卡组。"""
        name = (deck_name or "").strip().lower()
        by_lower = {k.lower(): v for k, v in self.mine.items()}
        while name:
            if name in by_lower:
                return by_lower[name]
            if "-" not in name:
                return None
            name = name.rsplit("-", 1)[0]
        return None

    def prompt_notes(self, deck_name: str, hand_ids, opp_cards) -> dict:
        """注入提示词的卡组要点（只含当前用得上的条目，可能为空 dict）。"""
        out: dict = {}
        mine = self.my_deck(deck_name)
        if mine:
            out["my_deck_plan"] = mine.get("plan", "")
            tips = {c: mine["cards"][c] for c in dict.fromkeys(hand_ids) if c in mine.get("cards", {})}
            if tips:
                out["hand_tips"] = tips
        opp = []
        for aid in self.match_opponent(opp_cards)[:2]:
            a = self.opponents[aid]
            opp.append({"archetype": a.get("name", aid), "plan": a.get("plan", ""), "counter": a.get("counter", "")})
        if opp:
            out["opponent_deck"] = opp
        return out

    # ------------------------------------------------------------ 赛后积累
    def record_game(self, game_id: str, opp_cards, result: str | None) -> str | None:
        """把本局对手揭示的牌记入命中的原型；返回原型 id。未命中写入候选日志。"""
        cards = sorted(set(c for c in opp_cards if c))
        if not cards:
            return None
        with self._lock:
            matched = self.match_opponent(cards)
            if not matched:
                try:
                    with (self.root / "unmatched.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"game_id": game_id[:8], "date": date.today().isoformat(), "result": result,
                                            "cards": cards}, ensure_ascii=False) + "\n")
                except OSError as e:
                    log.warning("写对手卡组候选失败：%r", e)
                return None
            aid = matched[0]
            a = self.opponents[aid]
            samples = [s for s in a.setdefault("samples", []) if s.get("game_id") != game_id[:8]]
            samples.append({"game_id": game_id[:8], "date": date.today().isoformat(), "result": result, "cards": cards})
            a["samples"] = samples[-MAX_SAMPLES:]
            seen = a.setdefault("seen_cards", {})
            for c in cards:
                seen[c] = seen.get(c, 0) + 1
            try:
                (self.root / "opponents.json").write_text(json.dumps(self.opponents, ensure_ascii=False, indent=2) + "\n", "utf-8")
            except OSError as e:
                log.warning("写对手卡组库失败：%r", e)
            self._index()
            return aid


_default: DeckKB | None = None


def default_kb() -> DeckKB:
    global _default
    if _default is None:
        _default = DeckKB()
    return _default

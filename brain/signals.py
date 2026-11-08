"""放牌顺序信号：某张牌 / 某个场地让「下一张（打到这里的）牌」得到好处或坏处时，本回合的出牌顺序就很关键。

知识库 data/signals.json 按 id 索引（locations / cards），每条 {scope, polarity, note}：
- scope  = "here"：下一张打到这一路的牌受影响（如 Castle Zemo：下一张打到这里的牌换到对手一侧）
           "global_here"：我方下一张牌（全局第一张）打到这一路才受影响（如 Shuri：打到这里则加倍）
           "global"：我方下一张牌（不限位置）受影响（如 Forge、Zero）
- polarity = "good" / "bad"
资料库里没收录、但能力文字是这一类的牌 / 场地，第一次遇到时自动识别并写入知识库（source=auto），知识库持续积累。

生效时机：卡牌信号只在它打出后的下一回合有效；场地信号从揭示起有效，直到我方在揭示后往那一路打过牌。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .kbfile import update_json
from .models import GameState

SIGNALS_PATH = Path(__file__).resolve().parent.parent / "data" / "signals.json"

_NEXT = re.compile(r"\bnext card\b|\bfirst card\b", re.I)
_HERE = re.compile(r"\bhere\b|at this location", re.I)
_BAD = re.compile(r"switch(?:es)? sides|enemy side|opponent's side|destroy|discard|remove the abilit|can't|-\d+ Power|lose", re.I)


def classify(text: str) -> dict | None:
    """能力 / 场地文字 -> 信号 {scope, polarity}；不是「下一张牌」类返回 None。"""
    if not text or not _NEXT.search(text):
        return None
    here = bool(_HERE.search(text))
    play_here = bool(re.search(r"(?:play|played)\b[^.]*\b(?:here|at this location)", text, re.I))
    if here and re.search(r"your next card at|next card you play here|card played here", text, re.I):
        scope = "here" if re.search(r"next card you play here|card played here", text, re.I) else "global_here"
    elif here and play_here:
        scope = "global_here"
    elif here:
        scope = "here"
    else:
        scope = "global"
    return {"scope": scope, "polarity": "bad" if _BAD.search(text) else "good"}


class SignalKB:
    def __init__(self, path: Path = SIGNALS_PATH):
        self.path = path
        self._lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("locations", {})
        self.data.setdefault("cards", {})

    def get(self, kind: str, sid: str, text: str) -> dict | None:
        """查信号；知识库没有但文字是这一类的，自动写入知识库。"""
        table = self.data[kind]
        if sid in table:
            return table[sid]
        sig = classify(text)
        if sig is None:
            return None
        sig = {**sig, "note": text, "source": "auto"}
        with self._lock:
            table[sid] = sig

            def add(data: dict) -> None:   # 写进磁盘最新版本，不覆盖面板里的人工修改
                data.setdefault("locations", {})
                data.setdefault("cards", {})
                data[kind].setdefault(sid, sig)

            try:
                fresh = update_json(self.path, add)
                for k in ("locations", "cards"):
                    self.data[k] = {**fresh.get(k, {}), **{x: v for x, v in self.data[k].items() if x not in fresh.get(k, {})}}
                return self.data[kind].get(sid, sig)
            except OSError:
                pass
        return sig


_kb: SignalKB | None = None


def default_kb() -> SignalKB:
    global _kb
    if _kb is None:
        _kb = SignalKB()
    return _kb


def active(state: GameState, db, kb: SignalKB | None = None) -> list[dict]:
    """本回合生效的放牌顺序信号：[{loc, scope, polarity, note, source}]（loc 为 None 表示不限位置）。"""
    kb = kb or default_kb()
    out = []
    last = state.turn - 1
    for loc in state.locations:
        if loc.revealed and loc.name and not state.played_here_since_reveal.get(loc.idx):
            sig = kb.get("locations", loc.name, db.location_effect(loc.name) or "")
            if sig:
                out.append({**sig, "loc": loc.idx, "source_id": loc.name})
        for c in loc.my_cards:
            if c.turn_played == last and last >= 1:
                sig = kb.get("cards", c.id, db.ability(c.id) or "")
                if sig:
                    out.append({**sig, "loc": loc.idx if sig["scope"] != "global" else None, "source_id": c.id})
    return out


def notes(state: GameState, db, where) -> list[str]:
    """关键信息栈：【放牌顺序】什么时候（本回合）、哪一路、怎么排。"""
    out = []
    for s in active(state, db):
        place = where(s["loc"], state.locations[s["loc"]].name) if s["loc"] is not None else "任意一路"
        if s["scope"] == "here":
            how = "打到这里的第一张放" + ("最弱的牌" if s["polarity"] == "bad" else "最想加强的牌")
        elif s["scope"] == "global_here":
            how = "本回合第一张牌就打到这里，放最强的牌" if s["polarity"] == "good" else "本回合第一张别打到这里"
        else:
            how = "本回合第一张放" + ("影响最小的牌" if s["polarity"] == "bad" else "最想加强的牌")
        out.append(f"【放牌顺序·本回合】{place} {s['source_id']}：{s['note']} → {how}")
    return out


def reorder(state: GameState, decision, db) -> str | None:
    """代码兜底：按生效信号调整 decision.actions 顺序。返回调整说明，无调整返回 None。"""
    if not decision.actions:
        return None
    power = {h.id: h.power for h in state.hand}
    msgs = []
    for s in active(state, db):
        if s["loc"] is None:
            continue
        idx = [i for i, a in enumerate(decision.actions) if a.location == s["loc"]]
        if not idx:
            continue
        if s["scope"] == "here":
            pick = (min if s["polarity"] == "bad" else max)(idx, key=lambda i: power.get(decision.actions[i].card, 0))
            if pick != idx[0]:
                a = decision.actions.pop(pick)
                decision.actions.insert(idx[0], a)
                msgs.append(f"{s['source_id']}：{a.card} 调到地点{s['loc']}的第一张")
        elif s["scope"] == "global_here" and s["polarity"] == "good":
            pick = max(idx, key=lambda i: power.get(decision.actions[i].card, 0))
            if pick != 0:
                a = decision.actions.pop(pick)
                decision.actions.insert(0, a)
                msgs.append(f"{s['source_id']}：{a.card} 调到本回合第一张（地点{s['loc']}）")
    return "；".join(msgs) or None

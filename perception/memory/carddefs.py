"""从国服客户端内存读取卡牌定义（只读，不注入、不写内存）。

实测（2026-11-09，SnapCN 48.18）：
- `CubeDef.CardDef` 实例：CardDefId、Name、Description（已按客户端语言本地化）、Cost、Power。
  描述是带占位符的模板，例如 ``+{card.AddedPower} Power``。
- `CubeGame.CardBehaviorDef`：CardDefId + Abilities（TriggerAbilityDef / OngoingAbilityDef…，带 Type，如 EndOfTurnTrigger）。
- 占位符的数值写死在技能代码里（静态 lambda），内存里没有数据表。客户端渲染过的卡牌文本会以代入数值后的字符串
  留在内存里，用模板反推即可拿到数值（约一半以上的卡）。其余由上层用国际服数值兜底或标记为未知。
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

from .il2cpp import Il2Cpp
from .process import MEM_PRIVATE, Process

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
# UTF-16LE 的「文本字符」：ASCII 可见字符 / 换行，以及常见标点 ’ ‘ “ ” – — • …
_UNIT = rb"(?:[\x09\x0a\x20-\x7e]\x00|[\x18\x19\x1c\x1d\x13\x14\x22\x26]\x20)"
_RUN = re.compile(_UNIT + rb"{12,900}")
_BOLD = "<b>".encode("utf-16-le")


@dataclass
class CardDefRow:
    id: str
    name: str
    template: str
    cost: int
    power: int
    abilities: list[str] = field(default_factory=list)   # CardBehaviorDef 里的技能类型，如 EndOfTurnTrigger


# ---------------------------------------------------------------------------
# 纯文本处理（无需游戏进程，便于测试）

def clean_text(text: str | None) -> str:
    """去掉富文本标签、换行，压缩空白。"""
    t = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _norm(text: str) -> str:
    """统一引号、空白（不去首尾：模板片段两端的空格有意义）。"""
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("‘", "'"))


def is_flavor(template: str) -> bool:
    """白板卡的 Description 是台词（引号包起来的一句话），不是能力。"""
    t = clean_text(template)   # 原文形如 <i>"HULK SMASH!"</i>
    return bool(t) and t[0] in "\"“" and "<b>" not in (template or "")


def is_placeholder(template: str) -> bool:
    """未完成 / 未上线的占位定义（原文 <color=#ff2c2c>!</color>none、none）。"""
    return clean_text(template).lower() in ("!none", "none")


def template_regex(template: str) -> tuple[re.Pattern, list[str]]:
    """模板 -> (正则, 占位符名)。名字类占位符（xxx.Name）匹配任意文字，其余匹配整数。"""
    parts = PLACEHOLDER.split(template)
    rx, names = "", []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            rx += re.escape(_norm(part))
        else:
            names.append(part)
            last = i == len(parts) - 2 and not parts[-1]
            rx += (r"(.+)" if last else r"(.+?)") if part.endswith("Name") else r"(-?\d+)"
    return re.compile(rx), names


def match_params(template: str, texts) -> dict[str, str] | None:
    """在一批已渲染文本里找与模板对应的那条，返回占位符取值；多条结果取出现最多的那组。"""
    if not PLACEHOLDER.search(template):
        return {}
    rx, names = template_regex(template)
    votes: collections.Counter = collections.Counter()
    for t in texts:
        m = rx.search(_norm(t).strip())
        if m:
            votes[m.groups()] += 1
    if not votes:
        return None
    return dict(zip(names, votes.most_common(1)[0][0]))


def render(template: str, params: dict[str, str] | None, unknown: str = "?") -> str:
    params = params or {}
    return PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), unknown)), template)


def keywords(template: str) -> list[str]:
    """模板里加粗的关键词：On Reveal / Ongoing / Activate / End of Turn / Game Start …"""
    return sorted({k.strip().rstrip(":").strip() for k in re.findall(r"<b>([^<]{2,40}?)</b>", template or "")} - {""})


# ---------------------------------------------------------------------------
# 内存读取

class CardDefReader:
    def __init__(self, process: Process | str = "SnapCN.exe"):
        self.p = process if isinstance(process, Process) else Process(process)
        self.il = Il2Cpp(self.p)

    def close(self) -> None:
        self.p.close()

    def _str(self, ptr: int) -> str | None:
        return self.il.string(ptr) if ptr else None

    def read_defs(self) -> dict[str, CardDefRow]:
        il, p = self.il, self.p
        found = il.find_classes([("CardDef", "CubeDef"), ("CardBehaviorDef", "CubeGame")])
        k_def = (found.get(("CardDef", "CubeDef")) or [None])[0]
        k_beh = (found.get(("CardBehaviorDef", "CubeGame")) or [None])[0]
        if not k_def:
            raise RuntimeError("未定位到 CubeDef.CardDef 类（客户端版本变了？）")
        f = il.fields(k_def)

        def off(name: str) -> int:
            fld = f.get(f"<{name}>k__BackingField") or f.get(name)
            if fld is None:
                raise RuntimeError(f"CardDef 缺少字段 {name}")
            return fld.offset

        o_id, o_name, o_desc, o_cost, o_pow = (off(n) for n in ("CardDefId", "Name", "Description", "Cost", "Power"))
        hits = p.find_u64([k for k in (k_def, k_beh) if k])
        rows: dict[str, CardDefRow] = {}
        for o in hits.get(k_def, []):
            if p.ptr(o + 8):   # monitor 非空：不是对象头
                continue
            cost, power = p.i32(o + o_cost), p.i32(o + o_pow)
            if cost is None or power is None or not (0 <= cost <= 30 and -100 <= power <= 100):
                continue
            cid = self._str(p.ptr(o + o_id))
            if not cid or not cid.isascii() or not re.fullmatch(r"[A-Za-z0-9_]+", cid):
                continue
            name = self._str(p.ptr(o + o_name)) or cid
            rows.setdefault(cid, CardDefRow(cid, name, self._str(p.ptr(o + o_desc)) or "", cost, power))
        if k_beh:
            for o in hits.get(k_beh, []):
                if p.ptr(o + 8):
                    continue
                cid = self._str(il.get_ptr(o, "CardDefId"))
                if cid not in rows:
                    continue
                types = [il.get_str(a, "Type") for a in il.list_ptrs(il.get_ptr(o, "Abilities"), limit=32)]
                rows[cid].abilities = sorted({t for t in types if t})
        return rows

    def rendered_texts(self) -> set[str]:
        """内存里所有含 <b> 的、已代入数值的卡牌文本（不含占位符）。"""
        out: set[str] = set()
        for addr, data in self.p._chunks((MEM_PRIVATE,), None):
            i = data.find(_BOLD)
            while i >= 0:
                lo = max(i - 1800, 0)
                lo += (i - lo) % 2   # 与命中位置同奇偶，保证按 UTF-16 对齐
                for m in _RUN.finditer(data, lo, min(i + 1800, len(data))):
                    if m.start() <= i < m.end():
                        try:
                            s = m.group().decode("utf-16-le")
                        except UnicodeDecodeError:
                            s = ""
                        if "{" not in s:
                            out.add(s)
                        i = m.end() - 6
                        break
                i = data.find(_BOLD, i + 6)
        return out

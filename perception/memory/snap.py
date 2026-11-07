"""从游戏进程内存（只读）读取实时对局状态。

可见性原则（与 Snapper 相同）：只输出玩家在画面上本来就能看到的信息。
- 对手：未翻开的牌只给数量；仅已揭示 / 进入弃牌或放逐区 / 明确对我方可见的牌才读取 CardDefId、费用、战力
- 我方牌库：只给剩余卡牌的**无序**集合（玩家本就知道自己卡组），不暴露抽牌顺序
- 未揭示的地点不读取 LocationDefId
"""
from __future__ import annotations

import time

from brain.models import Card, GameState, Location

from .il2cpp import Il2Cpp
from .process import Process

UNREVEALED_PREFIXES = ("RevealOn", "Unrevealed", "Hidden")
ZONES = ["Invalid", "Deck", "Hand", "Location", "Graveyard", "SetAside", "Banished"]
CLASSES = [
    ("Game", "CubeGame"),
    ("ClientGameInfo", "CubeUnity.App.State"),
    ("Card", "CubeGame"),
    ("Player", "CubeGame"),
    ("Location", "CubeGame"),
    ("GameManager", None),
    ("TutorialManager", "CubeUnity.App.Tutorials"),
]


class NotInGame(RuntimeError):
    pass


class SnapMemoryReader:
    def __init__(self, process_name: str = "SnapCN.exe", rescan_interval: float = 5.0):
        self.p = Process(process_name)
        self.il = Il2Cpp(self.p)
        self.classes: dict = {}
        self.game = 0
        self.game_id: str | None = None
        self.local_id: int | None = None
        self.rescan_interval = rescan_interval
        self._last_scan = 0.0
        self._stale_ids: set[str] | None = None
        self._tutorial_mgr = 0
        self._tutorial_scan = 0.0

    def close(self) -> None:
        self.p.close()

    # ------------------------------------------------------------ 定位
    def resolve_classes(self) -> dict:
        self.classes = self.il.find_classes(CLASSES)
        return self.classes

    def _game_klasses(self) -> list[int]:
        if not self.classes:
            self.resolve_classes()
        ks = self.classes.get(("Game", "CubeGame")) or []
        if not ks:
            raise RuntimeError("未定位到 CubeGame.Game 类（是否已进入过对局？）")
        return ks

    def _is_game(self, g: int) -> bool:
        il = self.il
        turn = il.get_int(g, "Turn")
        players = il.list_ptrs(il.get_ptr(g, "_players"), limit=8)
        gid = il.get_str(g, "Id")
        return turn is not None and 0 <= turn <= 20 and len(players) == 2 and bool(gid) and len(gid) >= 32

    def find_games(self) -> list[dict]:
        """扫描堆中所有 Game 实例，返回摘要列表（调试/选择用）。"""
        il = self.il
        out = []
        for k, addrs in self.p.find_u64(self._game_klasses()).items():
            for g in addrs:
                if not self._is_game(g):
                    continue
                out.append({
                    "addr": g,
                    "id": il.get_str(g, "Id"),
                    "turn": il.get_int(g, "Turn"),
                    "total_turns": il.get_int(g, "TotalTurns"),
                    "client_state": il.get_bool(g, "ClientState"),
                    "finished": self._finished(g),
                    "league": il.get_str(g, "LeagueDefId"),
                    "scenario": il.get_str(g, "ScenarioDefId"),
                })
        return out

    def _local_player_id(self, player_ids: set[int]) -> int | None:
        ks = self.classes.get(("ClientGameInfo", "CubeUnity.App.State")) or []
        votes: dict[int, int] = {}
        for k, addrs in (self.p.find_u64(ks).items() if ks else []):
            for a in addrs:
                lid = self.il.get_int(a, "LocalPlayerEntityId")
                if lid in player_ids:
                    votes[lid] = votes.get(lid, 0) + 1
        return max(votes, key=votes.get) if votes else None

    def _finished(self, g: int) -> bool:
        # 注意：Winner 在对局中就会被赋值（当前领先方），不能用来判断结束
        return bool(self.il.get_ptr(g, "ClientResultMessage") or self.il.get_bool(g, "_resultCreated"))

    def _start_ticks(self, g: int) -> int:
        f = self.il.field(g, "StartTimestamp")  # DateTime 值类型，8 字节
        return (self.p.i64(g + f.offset) or 0) if f else 0

    def select_game(self, force: bool = False) -> bool:
        """选当前对局：未结束 > 客户端状态 > 开局时间新 > 回合数大。

        当前对局有效且未结束 -> 直接复用（不扫描）；已结束 -> 每 rescan_interval 秒重扫一次找新对局。
        """
        valid = bool(self.game) and self._is_game(self.game) and self.il.get_str(self.game, "Id") == self.game_id
        if valid and not force and not self._finished(self.game):
            return True
        now = time.time()
        if not force and now - self._last_scan < self.rescan_interval:
            return valid
        self._last_scan = now
        games = self.find_games()
        if not games:
            self.game = 0
            return False
        if self._stale_ids is None:  # 首次扫描时已结束的对局 = 历史残留，之后不再选
            self._stale_ids = {g["id"] for g in games if g["finished"]}
        fresh = [g for g in games if g["id"] not in self._stale_ids] or games
        fresh.sort(
            key=lambda g: (not g["finished"], g["id"] == self.game_id, bool(g["client_state"]), g["turn"] or 0),
            reverse=True,
        )
        best = fresh[0]
        self.game, self.game_id = best["addr"], best["id"]
        ids = {self.il.get_int(p, "EntityId") for p in self.il.list_ptrs(self.il.get_ptr(self.game, "_players"))}
        self.local_id = self._local_player_id(ids)
        return True

    # ------------------------------------------------------------ 新手指引
    def _is_tutorial_mgr(self, m: int) -> bool:
        return self.il.object_class_name(self.il.get_ptr(m, "IsRunningTutorial")).startswith("ObservableProperty")

    def tutorial_running(self) -> bool | None:
        """新手指引是否正在进行（指引期间游戏会屏蔽拖牌等操作）。读不到返回 None。

        TutorialManager.IsRunningTutorial (ObservableProperty<bool>._value) 或 _runningTutorials 非空。
        """
        if not self.classes:
            self.resolve_classes()
        il = self.il
        if not (self._tutorial_mgr and self._is_tutorial_mgr(self._tutorial_mgr)):
            self._tutorial_mgr = 0
            if time.time() - self._tutorial_scan < 10:
                return None
            self._tutorial_scan = time.time()
            ks = self.classes.get(("TutorialManager", "CubeUnity.App.Tutorials")) or []
            for _, addrs in (self.p.find_u64(ks).items() if ks else []):
                for a in addrs:
                    if self._is_tutorial_mgr(a):
                        self._tutorial_mgr = a
                        break
            if not self._tutorial_mgr:
                return None
        m = self._tutorial_mgr
        flag = il.get_bool(il.get_ptr(m, "IsRunningTutorial"), "_value")
        running = il.list_ptrs(il.get_ptr(m, "_runningTutorials"), limit=64)
        return bool(flag) or bool(running)

    # ------------------------------------------------------------ 读取
    def _stat(self, obj: int) -> int | None:
        return self.il.get_int(obj, "Value") if obj else None

    def _zone_name(self, zone: int) -> str:
        z = self.il.get_int(zone, "ZoneId") if zone else None
        return ZONES[z] if z is not None and 0 <= z < len(ZONES) else f"Zone{z}"

    def _card(self, c: int) -> dict:
        il = self.il
        owner = il.get_int(il.get_ptr(c, "Owner"), "EntityId")
        zone = self._zone_name(il.get_ptr(c, "_zone"))
        revealed = il.get_bool(c, "Revealed", "IsRevealed")
        if revealed is None:
            tr = il.get_int(c, "TurnRevealed")
            revealed = bool(tr and tr > 0)
        visible_to = il.list_ints(il.get_ptr(c, "_visibleToPlayerEntityIds"), limit=8)
        mine = owner == self.local_id
        visible = mine or revealed or zone in ("Graveyard", "Banished") or (self.local_id in visible_to)
        out = {"entity": il.get_int(c, "EntityId"), "owner": "me" if mine else "opp", "zone": zone,
               "revealed": revealed, "pos": il.get_int(c, "ZonePosition")}
        if visible:
            out.update(
                id=il.get_str(c, "CardDefId"),
                cost=self._stat(il.get_ptr(c, "Cost")),
                power=self._stat(il.get_ptr(c, "Power")),
            )
        return out

    def snapshot(self) -> dict:
        if not self.select_game():
            raise NotInGame("当前不在对局中")
        il, g = self.il, self.game
        players = il.list_ptrs(il.get_ptr(g, "_players"))
        pid = [il.get_int(p, "EntityId") for p in players]
        if self.local_id not in pid:
            raise NotInGame("无法确定我方玩家")
        me_idx = pid.index(self.local_id)
        turn, finished = il.get_int(g, "Turn"), self._finished(g)
        me, opp = players[me_idx], players[1 - me_idx]

        def zone_cards(pl: int, zone: str) -> list[int]:
            return il.list_ptrs(il.get_ptr(il.get_ptr(pl, zone), "_cards"), limit=64)

        hand = sorted((self._card(c) for c in zone_cards(me, "Hand")), key=lambda c: c["pos"] if c["pos"] is not None else 99)

        def seen_ids(zone: str) -> list[str]:
            return [c["id"] for c in (self._card(x) for x in zone_cards(me, zone)) if c.get("id")]
        # 客户端不知道我方牌库具体是哪些牌（CardDefId 为 "None"），只给数量；剩余牌 = 卡组 − 已见牌，由上层计算
        deck_count = len(zone_cards(me, "Deck"))
        locs = []
        for i, loc in enumerate(il.list_ptrs(il.get_ptr(g, "_locations"), limit=3)):
            p1 = il.get_int(loc, "CurPlayer1Power") or 0
            p2 = il.get_int(loc, "CurPlayer2Power") or 0
            # 客户端在揭示前的 LocationDefId 本身就是占位名（如 "RevealOn2"），即画面所见；
            # LocationDefIdToReveal（未揭示地点的真身）永不读取。
            loc_id = il.get_str(loc, "LocationDefId")
            loc_revealed = bool(loc_id) and not loc_id.startswith(UNREVEALED_PREFIXES)
            cards = [self._card(c) for c in il.list_ptrs(il.get_ptr(loc, "_cards"), limit=16)]
            locs.append({
                "idx": i,
                "id": loc_id if loc_revealed else None,
                "raw_id": loc_id,
                "revealed": loc_revealed,
                "my_power": p1 if me_idx == 0 else p2,
                "opp_power": p2 if me_idx == 0 else p1,
                "my_cards": [c for c in cards if c["owner"] == "me"],
                "opp_cards": [c for c in cards if c["owner"] == "opp"],
            })
        return {
            "game_id": self.game_id,
            "turn": turn,
            "total_turns": il.get_int(g, "TotalTurns"),
            "cube_value": il.get_int(g, "CubeValue"),
            "no_turn_timer": il.get_bool(g, "NoTurnTimer"),
            "finished": self._finished(g),
            "me": {
                "entity": self.local_id,
                "energy": il.get_int(me, "CurrentEnergy"),
                "max_energy": il.get_int(me, "MaxEnergy"),
                "ended_turn": il.get_int(me, "EndedTurn"),
                "hand": hand,
                "deck_count": deck_count,
                "graveyard": seen_ids("Graveyard"),
                "banished": seen_ids("Banished"),
            },
            "opp": {
                "entity": pid[1 - me_idx],
                "hand_count": len(zone_cards(opp, "Hand")),
                "deck_count": len(zone_cards(opp, "Deck")),
            },
            "locations": locs,
        }


def _model_card(c: dict, **kw) -> Card:
    return Card(id=c.get("id") or f"unknown_{c['entity']}", cost=c.get("cost") or 0, power=c.get("power") or 0,
                entity=c.get("entity"), **kw)


def remaining_deck(deck: list[str], snap: dict) -> list[str]:
    """剩余牌库 = 卡组 − 已见过的我方牌（手牌、场上、弃牌、放逐）。效果生成的额外牌不在卡组里，自然忽略。"""
    left = list(deck)
    seen = [c.get("id") for c in snap["me"]["hand"]]
    seen += [c.get("id") for l in snap["locations"] for c in l["my_cards"]]
    seen += snap["me"].get("graveyard", []) + snap["me"].get("banished", [])
    for cid in seen:
        if cid in left:
            left.remove(cid)
    return sorted(left)


def to_game_state(snap: dict, deck: list[str] | None = None) -> GameState:
    """内存快照 -> 决策层的 GameState。deck：当前卡组的 CardDefId 列表（用于推算剩余牌库）。"""
    hand = [_model_card(c, hand_index=i) for i, c in enumerate(snap["me"]["hand"])]
    locations = [
        Location(
            idx=l["idx"],
            name=l["id"] or "",
            revealed=bool(l["revealed"] is not False),
            my_power=l["my_power"],
            opp_power=l["opp_power"],
            my_cards=[_model_card(c) for c in l["my_cards"]],
            opp_card_count=len(l["opp_cards"]),
            opp_cards=[_model_card(c) for c in l["opp_cards"] if c.get("id")],  # 仅已揭示的
        )
        for l in snap["locations"]
    ]
    return GameState(
        turn=snap["turn"] or 0,
        max_turn=snap["total_turns"] or 6,
        energy=snap["me"]["energy"] or 0,
        cube_stake=snap["cube_value"] or 1,
        turn_timer=not snap.get("no_turn_timer", False),
        hand=hand,
        deck_remaining=remaining_deck(deck, snap) if deck else [],
        my_deck_count=snap["me"].get("deck_count"),
        my_graveyard=list(snap["me"].get("graveyard", [])),
        opp_hand_count=(snap.get("opp") or {}).get("hand_count"),
        opp_deck_count=(snap.get("opp") or {}).get("deck_count"),
        locations=locations,
    )

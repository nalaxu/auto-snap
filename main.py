"""Auto-Snap 入口。

    python main.py observe                      # 观察模式：感知 -> 决策，只打印建议，不操作鼠标
    python main.py auto -v                      # 自动模式演练：打印每个鼠标动作，不动鼠标
    python main.py auto --live --max-turns 1 -v # 实操：只自动打 1 个回合（按住 F8 中止）
    python main.py clients                      # 列出可用客户端及运行状态
    python main.py auto --client cn_official -v # 指定客户端（默认取 config 的 [game].client，可设 SNAP_CLIENT）
    python main.py ui                           # 外部面板：浏览器里启停、看实时对局（http://127.0.0.1:8765）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent


def load_settings() -> dict:
    with open(ROOT / "config" / "settings.toml", "rb") as f:
        return tomllib.load(f)


def select_client(settings: dict, override: str | None = None):
    """解析当前客户端，并把客户端相关的全局设置（界面模板）切过去。"""
    import clients
    from executor import screens

    client = clients.resolve(settings, override)
    screens.set_template_dirs(client.template_dirs)
    running = "运行中" if client.running() else "未运行"
    print(f"客户端：{client.describe()}，{running}")
    if not client.verified:
        print("  ⚠ 该客户端尚未实测，内存读取 / 界面识别可能需要适配（先用 observe 模式核对）")
    return client


def list_clients(settings: dict) -> None:
    import clients

    current = clients.selected_key(settings)
    print(f"当前选择：{current}（命令行 --client / 环境变量 SNAP_CLIENT / config [game].client）")
    for key, c in clients.profiles(settings).items():
        pids = c.find_pids()
        mark = "*" if key == current else " "
        state = f"运行中 pid={pids}" if pids else "未运行"
        print(f" {mark} {key:12s} {c.label}  进程 {c.process_name}  {state}"
              f"  {'已实测' if c.verified else '未实测'}  卡组目录 {'✓' if c.states_dir.exists() else '✗'} {c.states_dir}")


def make_decider(settings: dict):
    """-> (decide 函数, 决策器对象或 None)。decider = "llm" 时用大模型（含场地分析），未配置密钥则退回规则 AI。"""
    from brain import llm, rule_based

    if settings.get("brain", {}).get("decider") == "llm":
        d = llm.make_decider(settings)
        if d is not None:
            return d.decide, d
        print("⚠ brain.decider = llm 但未配置大模型密钥，改用规则 AI")
    return rule_based.decide, None


def observe(settings: dict, interval: float = 0.5, client=None) -> None:
    from brain.validator import validate
    from perception import make_state_source

    source = make_state_source(settings, client)
    decide, _ = make_decider(settings)
    last_key = None
    print("观察模式：轮到我方出牌时打印局面与建议（Ctrl+C 退出）")
    try:
        while True:
            state = source.read_state()
            if state is None:
                last_key = None
                time.sleep(interval)
                continue
            key = (state.turn, state.energy, tuple(c.id for c in state.hand),
                   tuple((l.my_power, l.opp_power, len(l.my_cards), l.opp_card_count) for l in state.locations))
            if key != last_key:  # 同一局面只决策一次
                last_key = key
                print(f"\n=== T{state.turn}/{state.max_turn} 能量 {state.energy} 赌注 {state.cube_stake}")
                print("  手牌: " + ", ".join(f"[{c.hand_index}]{c.id}({c.cost}/{c.power})" for c in state.hand))
                for l in state.locations:
                    opp = ", ".join(c.id for c in l.opp_cards) or "-"
                    print(f"  地点{l.idx} {l.name or '<未揭示>'}: 我 {l.my_power} : {l.opp_power} 敌"
                          f"  (我方 {len(l.my_cards)} 张 / 敌方 {l.opp_card_count} 张: {opp})")
                if state.deck_remaining:
                    print(f"  剩余牌库: {', '.join(state.deck_remaining)}")
                t = time.time()
                d = decide(state)
                errs = validate(state, d)
                acts = ", ".join(f"{a.card}→地点{a.location}" for a in d.actions) or "不出牌"
                print(f"  建议[{d.source} {1000 * (time.time() - t):.0f}ms]: {acts}"
                      + (" + SNAP" if d.snap else "") + (f"  ⚠ {errs}" if errs else ""))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(source, "close"):
            source.close()


# 对局外界面（arena_flow.step / menu_step 的返回值）-> 面板里报的名字；不在表里的（点击动作等）不报
MENU_NAMES = {
    "play": "主界面（PLAY）", "main_menu": "主界面", "game_modes": "模式选择", "arena_landing": "竞技场主页",
    "arena_board": "竞技场（组卡 / 整场）", "arena_ready": "竞技场（等待开局）", "arena_loading": "竞技场（读取状态中）",
    "arena_draft_wait": "竞技场组卡（等待候选）", "arena_swap_wait": "竞技场换牌（等待候选）",
    "arena_chest": "竞技场宝箱", "arena_claim": "竞技场领奖", "collect_rewards": "对局结算", "next": "对局结算",
}


def auto(settings: dict, live: bool, max_turns: int = 0, skip_current: bool = False, max_games: int = 0,
         client=None, events: bool = False, game_mode: str = "ranked", max_runs: int | None = None) -> None:
    """自动对局：感知 -> 决策 -> 执行 -> 结束回合；对局外按界面识别处理 结算(Collect Rewards) -> 奖励(Next) -> 主界面(Play)。

    events=True：向 stdout 输出结构化事件并从 stdin 接收控制命令（外部面板 ui/server 启动时使用，见 gamelog/live.py）。
    """
    from brain import rule_based
    from brain.knowledge import default_db
    from brain.validator import validate
    from executor.actions import ActionFailed, Executor, GameOver, PlayRejected
    from executor.arena_flow import ArenaStop
    from executor.mouse import Aborted, check_abort, request_abort
    from gamelog.live import Control, LiveBus, decision_payload, state_payload
    from perception import make_state_source

    bus = LiveBus(enabled=events)
    control = Control(on_stop=request_abort, bus=bus)
    if events:
        control.start_stdin_reader()
    stop_reason = "结束"

    from brain import strategy
    from brain.board import BoardTracker
    from brain.locations import LocationAdvisor, phase
    from gamelog.recorder import GameRecorder

    if client is None:
        client = select_client(settings)
    source = make_state_source(settings, client)
    advisor = LocationAdvisor()            # 知识库（场地分析已并入大模型逐回合决策）
    strategy.set_default_advisor(advisor)
    rec = GameRecorder(client=client.key)
    board = BoardTracker()                 # 场上要点（消息栈）：每回合重建
    decide, decider = make_decider(settings)
    ex = Executor(source.snapshot, client, dry_run=not live)
    arena_flow = None
    from perception.memory_source import is_arena_game
    from brain import arena_strategy
    from brain.arena_strategy import ArenaContext, ArenaModel
    arena_ctx: dict[str, ArenaContext | None] = {}   # game_id -> 竞技场整场背景
    from brain.location_rules import daredevil_watch
    daredevil_seen: set[tuple] = set()   # (game_id, turn)：已等过夜魔侠观察的回合
    if game_mode == "arena":
        source.arena_mode = True
        from brain.llm import make_arena_decider
        from executor.arena_flow import ArenaFlow, ArenaOptions
        from gamelog.arena_log import ArenaRunLog
        acfg = settings.get("arena", {})
        arena_flow = ArenaFlow(ex, source.arena_state, ArenaOptions(
            buy_ticket_with_gold=bool(acfg.get("buy_ticket_with_gold", False)),
            max_runs=int(max_runs if max_runs is not None else acfg.get("max_runs", 0)),
            swap_threshold=float(acfg.get("swap_threshold", 1.0))), emit=bus.emit, live=live,
            decider=make_arena_decider(settings) if acfg.get("draft_decider", "llm") == "llm" else None,
            run_log=ArenaRunLog())
        print(f"竞技场：组卡 / 换牌 {'大模型 ' + arena_flow.decider.task.model if arena_flow.decider else '规则打分'}；"
              f"买票 {'允许' if arena_flow.opt.buy_ticket_with_gold else '不允许'}；"
              f"场数上限 {arena_flow.opt.max_runs or '不限'}")
    snap_enabled = bool(settings.get("brain", {}).get("snap", True))
    activated_cards: set[tuple] = set()
    opp_seen: dict[str, list[str]] = {}    # game_id -> 对手揭示过的牌（卡组原型识别 + 赛后样本）
    from brain import decks   # (game_id, entity)：已激活过的场上牌
    retreat_enabled = bool(settings.get("brain", {}).get("retreat", True))
    retreat_threshold = settings.get("brain", {}).get("retreat_win_prob")   # None = 按期望方块计算
    from brain.llm.decider import retreat_check
    done: set[tuple] = set()
    failures: dict[tuple, int] = {}
    forbidden: dict[tuple, list[tuple[str, int]]] = {}   # (game, turn) -> 被拒的 (card, 地点)
    turns = 0

    def handle_overlay() -> bool:
        """已知全屏提示（新卡展示 / Tap to SNAP 指引）：识别后点击对应位置。处理了返回 True。"""
        from executor.screens import OVERLAY_CLICKS

        if not live:
            return False
        try:
            from executor import screens as _scr
            img = ex.capture()
            m = _scr.current(img) if img is not None else None
            if m and m.name in OVERLAY_CLICKS and _scr.out_of_game(img):
                # 竞技场弹窗左下也有「Esc Close」，会被认成 card_detail；画面不在对局里时不点（否则会把竞技场界面关掉）
                return False
            if m and m.name in OVERLAY_CLICKS:
                print(f"  ☞ 识别到 {m.name}（{m.score:.2f}），点击 {OVERLAY_CLICKS[m.name]}")
                log_event("overlay", name=m.name, score=round(m.score, 3))
                ex.click_rel(OVERLAY_CLICKS[m.name], m.name)
                time.sleep(1.2)
                return True
        except ActionFailed as e:
            print(f"  ⚠ 处理提示失败：{e}")
        return False

    def guide_active() -> bool:
        """新手指引：内存标志（TutorialManager）或画面遮罩（整屏模糊，如 Tap to SNAP）。"""
        return bool(getattr(source, "tutorial_running", lambda: False)()) or ex.overlay_active()

    def wait_tutorial(timeout_s: float = 180) -> None:
        """新手指引期间游戏屏蔽操作：暂停，等玩家跟随指引点击完成。"""
        if not live:
            return
        while handle_overlay():  # 先处理已知提示（可能连续出现多个）
            pass
        if not guide_active():
            return
        print("  ⏸ 检测到新手指引，请跟随指引点击（程序暂停等待，按住 F8 可中止）")
        log_event("guide_pause")
        prev_phase = bus.phase or "in_game"
        bus.set_phase("guide")
        try:
            end = time.time() + timeout_s
            while time.time() < end:
                check_abort()
                time.sleep(0.5)
                handle_overlay()
                if not guide_active():
                    print("  ▶ 指引结束，继续")
                    time.sleep(0.8)
                    return
            print("  ⚠ 等待指引超时，继续尝试")
        finally:
            bus.set_phase(prev_phase)

    played_games: set[str] = set()
    from concurrent.futures import ThreadPoolExecutor
    decide_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decide")

    def decide_watching(st, game_id: str):
        """后台决策，同时每 0.3s 看一眼内存：对局结束（如对手撤退）就放弃决策，返回 None。"""
        fut = decide_pool.submit(decide, st)
        while True:
            try:
                return fut.result(timeout=0.3)
            except TimeoutError:
                pass
            check_abort()
            cur = source.snapshot()
            if cur and cur.get("game_id") == game_id and cur.get("finished"):
                return None
    last_menu_check = 0.0
    last_overlay_check = 0.0
    loop_started = time.time()
    boot_games: set[str] = set()       # 启动时就在内存里的对局（可能是残留的幽灵对局）
    ghost_watch = {"game": None, "streak": 0, "at": 0.0}
    menu_watch = {"name": None, "since": time.time(), "warned": 0.0}

    def check_ghost(snap: dict) -> bool:
        """内存说对局进行中、但还没轮到我方：截图交叉验证。画面连续两次（间隔 ≥ 1.5s）明确是主界面 / 竞技场界面
        -> 内存里是残留的幽灵对局（如上次中途退出、机器人被停掉），判掉。返回是否判了幽灵。
        刚开的新对局（T0/T1，匹配界面可能还盖着竞技场页面）不判，除非启动时就已存在；判错的话回合一推进内存层会自动恢复。"""
        now = time.time()
        if now - ghost_watch["at"] < 1.5:
            return False
        ghost_watch["at"] = now
        gid = snap["game_id"]
        if ghost_watch["game"] != gid:
            ghost_watch["game"], ghost_watch["streak"] = gid, 0
        if (snap.get("turn") or 0) < 2 and gid not in boot_games:
            return False
        try:
            from executor import screens as _scr
            img = ex.capture()
            where = _scr.out_of_game(img) if img is not None else None
        except Exception as e:  # noqa: BLE001
            log_event("ghost_check_error", error=repr(e))
            return False
        ghost_watch["streak"] = ghost_watch["streak"] + 1 if where else 0
        if ghost_watch["streak"] < 2:
            return False
        ghost_watch["streak"] = 0
        if source_mark_ghost(gid, f"画面是{where}"):
            print(f"  ↺ 内存里的对局 {gid[:8]}（T{snap.get('turn')}）是残留的旧对局：画面在{where}，已忽略")
            log_event("ghost_game", game_id=gid, turn=snap.get("turn"), screen=where)
            return True
        return False

    def source_mark_ghost(gid: str, reason: str) -> bool:
        fn = getattr(source, "mark_ghost", None)
        return bool(fn and fn(gid, reason))

    def watch_menu(name: str | None) -> None:
        """对局外界面看门狗：界面变化时在面板里报一句；长时间认不出界面（未知弹窗 / 窗口被挡 / 最小化）就提示并留截图，
        不再无声地原地打转。"""
        now = time.time()
        if name != menu_watch["name"]:
            if name in MENU_NAMES:
                print(f"  · 界面：{MENU_NAMES[name]}")
            menu_watch.update(name=name, since=now)
            return
        if name in (None, "unknown", "no_window") and now - menu_watch["since"] > 30 and now - menu_watch["warned"] > 60:
            menu_watch["warned"] = now
            path = ex.save_evidence("menu_unknown") if name != "no_window" else None
            what = "找不到游戏窗口（最小化或被关闭？）" if name == "no_window" else "认不出当前界面"
            print(f"  ⚠ 已经 {now - menu_watch['since']:.0f}s {what}：请把游戏切到主界面或竞技场界面"
                  + (f"（截图 {path}）" if path else ""))
            log_event("menu_stuck", screen=name, seconds=round(now - menu_watch["since"]))

    def reached_max() -> bool:
        return bool(max_games) and len(finished_games) >= max_games

    def want_more() -> bool:
        """还要开下一局吗：没到 --max-games，且面板没有暂停。"""
        return not reached_max() and not control.paused

    def menu_step(press_play: bool, check_guide: bool = True) -> str | None:
        """对局外：识别界面并点击下一步按钮。返回识别到的界面名。check_guide=False：不读内存查新手指引（启动时抢先点 PLAY）。"""
        m = ex.screen()
        if m is None:
            return None
        if m.name in ("collect_rewards", "next"):
            wait_tutorial()
            ex.click_rel(m.center, m.name)
            time.sleep(2.5)
        elif m.name == "play" and press_play:
            if check_guide:
                wait_tutorial()
            # 匹配界面（Searching For Opponent）没有 PLAY 按钮，所以只要 PLAY 还在就说明点击没生效，可以立即重点；
            # 不要在这里读内存（对局结束后的重扫会阻塞 10s 以上，拖慢重试）
            for attempt in range(1, 4):
                ex.click_rel(m.center, "PLAY" if attempt == 1 else f"PLAY（第 {attempt} 次）")
                end = time.time() + 4
                while time.time() < end:
                    time.sleep(0.5)
                    m2 = ex.screen()
                    if not m2 or m2.name != "play":
                        print("  ▶ 已点击 PLAY，匹配中…")
                        bus.set_phase("matching")
                        return m.name
                m = m2
            print("  ⚠ 连点 3 次 PLAY 仍停在主界面")
        return m.name

    def back_to_menu(timeout_s: float = 90) -> None:
        """对局结束后：Collect Rewards -> Next，直到回到主界面（不点 Play）；竞技场模式回到竞技场界面。"""
        end = time.time() + timeout_s
        if arena_flow is not None:
            while time.time() < end:
                try:
                    name = arena_flow.step(press_play=False)
                except ActionFailed as e:
                    print(f"  ⚠ 界面操作失败：{e}")
                    name = None
                if name in ("arena_board", "arena_ready", "arena_landing", "main_menu"):
                    print("  已回到竞技场界面")
                    return
                time.sleep(1)
            print("  ⚠ 未能回到竞技场界面（可能出现了未识别的弹窗），请手动处理")
            return
        while time.time() < end:
            try:
                screen_name = menu_step(press_play=False)
            except ActionFailed as e:
                print(f"  ⚠ 界面操作失败：{e}")
                screen_name = None
            if screen_name == "play":
                print("  已回到主界面")
                return
            time.sleep(1)
        print("  ⚠ 未能回到主界面（可能出现了未识别的弹窗），请手动处理")

    finished_games: set[str] = set()
    print(f"自动模式（{'实操' if live else '演练：不动鼠标'}）。按住 F8 随时中止。")
    if not live:
        print("  演练模式不会点 PLAY / 进入竞技场 / 组卡：需要手动开局；要自动打，请停止后选「实操」启动")
    if decider is not None:
        print(f"决策：大模型 {decider.client.provider}/{decider.task.model}（含场地分析）；有回合计时 effort={decider.effort_timed}、"
              f"{decider.deadline_timed_s:.0f}s 截止（T{decider.late_from_turn} 起 {decider.deadline_late_s:.0f}s），无计时 effort={decider.task.effort}、{decider.deadline_s:.0f}s 截止；"
              f"超时由规则 AI 兜底；对局记录 -> {rec.root}")
    else:
        print(f"决策：规则 AI（场地知识库）；对局记录 -> {rec.root}")
    db = default_db() if events else None
    bus.emit("session", live=live, client=client.key, client_label=getattr(client, "label", client.key),
             decider="llm" if decider is not None else "rule_based",
             model=f"{decider.task.model}" if decider is not None else "规则 AI",
             effort=(decider.effort_timed or decider.task.effort) if decider is not None else None,
             max_games=max_games, max_turns=max_turns, snap=snap_enabled, retreat=retreat_enabled, game_mode=game_mode)
    announced_games: set[str] = set()

    def log_event(kind: str, **data) -> None:
        cur = source.last_snapshot
        if cur and not cur.get("finished"):
            rec.event(cur["game_id"], kind, turn=cur.get("turn"), **data)

    def location_report(st) -> list[dict]:
        out = []
        for l in st.locations:
            info = advisor.info(l.name) if l.revealed and l.name else None
            out.append({"idx": l.idx, "name": l.name or None, "phase": phase(st.turn, st.max_turn),
                        "bias": info.bias_at(st.turn, st.max_turn) if info else 0,
                        "source": info.source if info else None, "effect": info.effect if info else None})
        return out
    if live and want_more() and not skip_current and arena_flow is None:
        # 先点 PLAY（只需截图识别，不读内存），附加进程 / 扫描内存放到匹配等待期间做
        try:
            m0 = ex.screen()
            if m0 and m0.name == "play":
                print("  ▶ 主界面：先点 PLAY，匹配期间再预热内存读取")
                menu_step(press_play=True, check_guide=False)
        except ActionFailed as e:
            print(f"  ⚠ 界面操作失败：{e}")
    if hasattr(source, "tutorial_running"):  # 预热：附加进程、定位类与 TutorialManager（有缓存时毫秒级）
        t0 = time.time()
        source.tutorial_running()
        print(f"  内存读取就绪（{time.time() - t0:.1f}s）")
    if arena_flow is not None:   # 启动时先推一次竞技场状态（面板立即显示血量 / 积分 / 卡组；演练模式也有）
        arena_flow.observe(source.arena_state())
    if skip_current:  # 当前回合留给玩家手动完成，从下一回合开始接管
        cur = source.snapshot()
        if cur and not cur.get("finished"):
            done.add((cur["game_id"], cur["turn"]))
            print(f"跳过当前回合 T{cur['turn']}，从 T{cur['turn'] + 1} 开始接管")
    try:
        while True:
            check_abort()
            state = source.read_state()
            snap = source.last_snapshot
            if snap and not snap.get("finished"):
                played_games.add(snap["game_id"])   # 见过进行中的对局：结束后（含对手 T1 撤退）立即领奖励
            if snap and snap.get("finished") and snap["game_id"] in played_games and snap["game_id"] not in finished_games:
                finished_games.add(snap["game_id"])
                summary = rec.end(snap) or {}
                from brain.calibration import default_calibrator
                default_calibrator().refresh()   # 新对局结果进入胜率校准样本
                final_opp = [c.get("id") for l in snap["locations"] for c in l["opp_cards"] if c.get("id")]
                arche = None
                if not is_arena_game(snap) and arena_flow is None:   # 竞技场对手是随机组卡，不进对手原型库
                    arche = decks.default_kb().record_game(snap["game_id"], opp_seen.get(snap["game_id"], []) + final_opp,
                                                           summary.get("result"))
                if arche:
                    print(f"  对手卡组：{decks.default_kb().opponents[arche].get('name', arche)}（已记入样本）")
                print(f"对局 {snap['game_id'][:8]} 结束：{summary.get('result')}（场地 {summary.get('my_locations_won')}:"
                      f"{summary.get('opp_locations_won')}，赌注 {summary.get('cube_value')}；已完成 {len(finished_games)} 局）")
                bus.emit("game_end", game_id=snap["game_id"], games_done=len(finished_games),
                         archetype=decks.default_kb().opponents[arche].get("name", arche) if arche else None,
                         **{k: summary.get(k) for k in ("result", "retreat", "my_locations_won", "opp_locations_won",
                                                         "cube_value", "turns_recorded")})
                if arena_flow is not None:
                    arena_flow.note_game(snap["game_id"], summary.get("result"), summary.get("cube_value"))
                bus.set_phase("menu")
                if live:
                    back_to_menu()
                if arena_flow is not None and arena_flow.reached_max_runs():
                    print("按 --max-runs 停止")
                    stop_reason = f"已打完 {arena_flow.opt.max_runs} 场竞技"
                    return
                if reached_max():
                    print("按 --max-games 停止")
                    stop_reason = f"已打完 {max_games} 局"
                    return
                if control.paused:
                    print("  ⏸ 面板已暂停：停在主界面，等待继续")
            if snap and time.time() - loop_started < 15:
                boot_games.add(snap["game_id"])
            if state is None or not snap:
                if decider is not None and hasattr(decider, "warmup"):
                    decider.warmup()           # 等待期间保持到模型服务的连接（新建连接实测 1.4-5s）
                in_game = bool(snap) and not snap.get("finished")
                if in_game and check_ghost(snap):
                    continue
                if in_game:
                    bus.set_phase("in_game")
                elif control.paused:
                    bus.set_phase("paused")
                elif bus.phase not in ("matching",):
                    bus.set_phase("menu")
                if live and in_game and time.time() - last_overlay_check > 1:  # 揭示阶段的新卡展示等
                    last_overlay_check = time.time()
                    handle_overlay()
                if live and not in_game and time.time() - last_menu_check > 2:
                    last_menu_check = time.time()
                    try:
                        if arena_flow is not None:
                            watch_menu(arena_flow.step(press_play=want_more() and not arena_flow.reached_max_runs()))
                            if arena_flow.reached_max_runs():
                                print("按 --max-runs 停止")
                                stop_reason = f"已打完 {arena_flow.opt.max_runs} 场竞技"
                                return
                        else:
                            watch_menu(menu_step(press_play=want_more()) or "unknown")
                    except ActionFailed as e:
                        print(f"  ⚠ 界面操作失败：{e}（稍后重试）")
                        last_menu_check = time.time() + 5
                time.sleep(0.3)
                continue
            key = (snap["game_id"], state.turn)
            if key in done:
                time.sleep(0.3)
                continue
            wait_tutorial()
            state = source.read_state() or state
            state.forbidden = forbidden.get(key, [])
            seen = opp_seen.setdefault(snap["game_id"], [])
            for l in state.locations:
                for c in l.opp_cards:
                    if c.id not in seen:
                        seen.append(c.id)
            state.opp_seen, state.deck_name = list(seen), getattr(source, "deck_name", "")
            if arena_flow is not None or is_arena_game(snap):   # 竞技场：整场背景（开局读一次，局内不变）
                if snap["game_id"] not in arena_ctx:
                    ast = source.arena_state()
                    arena_ctx[snap["game_id"]] = ArenaContext(
                        score=ast.score, hp=ast.hp,
                        model=ArenaModel.from_settings(settings, ast.config.max_score)) if ast and ast.phase == "run" else None
                    if arena_ctx[snap["game_id"]]:
                        c0 = arena_ctx[snap["game_id"]]
                        print(f"  ⚔ 竞技场对局：血量 {c0.hp}，积分 {c0.score}")
                actx = arena_ctx.get(snap["game_id"])
                if actx:
                    state.arena = actx.prompt(state.cube_next or state.cube_stake, state.turn >= state.max_turn)
            for l in state.locations:   # 本程序激活过的牌（内存里没有可靠的「已激活」标志）
                for c in l.my_cards:
                    c.activated = (snap["game_id"], c.entity) in activated_cards
            if live:   # 画面上的「可激活」提示：认出资料库里没写激活的牌（如 Iron Lad 复制到激活能力）
                try:
                    from executor import screens as _scr
                    from executor.layout import hint_to_slot
                    img = ex.capture()
                    cands = [(l.idx, c.slot) for l in state.locations for c in l.my_cards if c.slot is not None]
                    for h in (_scr.activate_hints(img) if img is not None else []):
                        if (hit := hint_to_slot(h, cands)):
                            for c in state.locations[hit[0]].my_cards:
                                if c.slot == hit[1]:
                                    c.screen_activatable = True
                except Exception as e:  # noqa: BLE001
                    log_event("activate_hint_error", error=repr(e))
            board.update(snap["game_id"], state)
            if live and key not in daredevil_seen and daredevil_watch(state):
                # 夜魔侠：第 5 回合先看对手出牌，观察期间按钮是「WAITING…」，放牌会被拒。等 END TURN 出现后重读局面再决策
                daredevil_seen.add(key)
                print("  ◎ 夜魔侠：等待对手第 5 回合出牌展示完再出牌")
                log_event("daredevil_wait")
                end = time.time() + 45
                while time.time() < end:
                    check_abort()
                    m = ex.screen()
                    if m and m.name == "end_turn":
                        break
                    time.sleep(0.5)
                time.sleep(0.5)
                continue
            if state.board_notes:
                print("  场上要点：\n    " + "\n    ".join(state.board_notes))
            bus.set_phase("in_game")
            if events:
                if snap["game_id"] not in announced_games:
                    announced_games.add(snap["game_id"])
                    bus.emit("game_start", game_id=snap["game_id"], deck_name=getattr(source, "deck_name", ""),
                             games_done=len(finished_games))
                state_dump = state.model_dump()
                deadline = decider.planned_deadline(state) if decider is not None and hasattr(decider, "planned_deadline") else 0.0
                bus.emit("turn_state", game_id=snap["game_id"], turn=state.turn,
                         state=state_payload(state_dump, location_report(state), db), deadline=round(deadline, 1))
            t_dec = time.time()
            d = decide_watching(state, snap["game_id"])
            if d is None:
                print(f"  ■ 对局已结束（{'对手撤退' if (source.last_snapshot or {}).get('opp', {}).get('conceded') else '结束'}），放弃本回合决策")
                done.add(key)
                continue
            invalid = validate(state, d)
            if invalid:
                d = rule_based.decide(state)
            from brain.board import reorder_for_next_card
            if note := reorder_for_next_card(state, d):
                print(f"  ↕ 出牌顺序调整：{note}")
            if d.snap and not snap_enabled:
                d.snap = False
                d.reason += "（Snap 已在 config [brain].snap 关闭）"
            dec_s = time.time() - t_dec
            if d.win_probability is not None:   # 历史校准：模型终局预估明显偏高（实测 73–80% 只赢 2/6）
                from brain.calibration import default_calibrator
                d.win_probability_raw = d.win_probability
                d.win_probability = default_calibrator().calibrate(d.win_probability, state.turn >= state.max_turn)
                if abs(d.win_probability - d.win_probability_raw) >= 0.03:
                    print(f"  胜率校准：模型 {d.win_probability_raw:.0%} → 历史校准 {d.win_probability:.0%}")
            actx = arena_ctx.get(snap["game_id"])
            if actx:
                want_retreat, retreat_why = arena_strategy.retreat_check(state, d.win_probability, actx)
                if d.snap:
                    veto, veto_why = arena_strategy.snap_veto(d.win_probability, actx, state.cube_next or state.cube_stake,
                                                              state.turn >= state.max_turn)
                    if veto:
                        d.snap = False
                        d.reason += f"（Snap 被否决：{veto_why}）"
                        print(f"  Snap 否决：{veto_why}")
            else:
                want_retreat, retreat_why = retreat_check(state, d.win_probability, retreat_threshold)
            if retreat_why:
                print(f"  撤退判断：{retreat_why} → {'撤退' if want_retreat else '继续'}")
            d.retreat = want_retreat and retreat_enabled
            acts = ", ".join(f"{a.card}→地点{a.location}" for a in d.actions) or "不出牌"
            stake = f" 赌注{state.cube_stake}" + ("（对手已 Snap）" if state.opp_snapped else "")
            if state.turn_seconds_left is not None:
                stake += f" 计时{state.turn_seconds_left:.0f}/{state.turn_seconds_total:.0f}s"
            if d.moves:
                acts = "移动 " + ", ".join(f"{m.card}:{m.from_location}→{m.to_location}" for m in d.moves) + "；" + acts
            if d.activate:
                acts = "激活 " + ", ".join(f"{a.card}@地点{a.location}" for a in d.activate) + "；" + acts
            print(f"T{state.turn} 能量{state.energy}{stake} [{d.source} {dec_s:.1f}s] {acts}" + ("  + SNAP" if d.snap else ""))
            if d.reason:
                print(f"    理由：{d.reason}")
            llm_raw = getattr(decider, "last_raw", None) if decider is not None else None
            if llm_raw and llm_raw.get("plan"):
                print(f"    规划：{llm_raw['plan']}")
            rec.start(snap["game_id"], getattr(source, "deck_name", ""), getattr(source, "deck", []),
                      mode="arena" if (arena_ctx.get(snap["game_id"]) or is_arena_game(snap)) else "ranked",
                      arena=state.arena)
            executed: list[dict] = []
            if events:
                bus.emit("decision", game_id=snap["game_id"], turn=state.turn,
                         decision=decision_payload(d.model_dump(), state_dump, llm_raw, round(dec_s, 2),
                                                   note=getattr(decider, "last_note", "") if decider is not None else "",
                                                   retreat_why=retreat_why or "", invalid=[str(e) for e in invalid or []],
                                                   db=db))

            def did(item: dict) -> None:
                executed.append(item)
                bus.emit("exec", game_id=snap["game_id"], turn=state.turn, **item)

            def record(outcome: str) -> None:
                rec.turn(snap["game_id"], state, d, location_report(state), executed, outcome,
                         llm=llm_raw, decide_s=round(dec_s, 2))
                bus.emit("turn_done", game_id=snap["game_id"], turn=state.turn, outcome=outcome)

            try:
                ex.prepare()
                if d.retreat:
                    try:
                        ex.retreat()
                        did({"retreat": True, "ok": True})
                        record("retreat")
                        done.add(key)
                        played_games.add(snap["game_id"])
                        print(f"  ⤺ 稍后撤退并结束回合：最多输当前赌注 {state.cube_stake}（对手同时撤退则平局）")
                        continue
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        did({"retreat": True, "ok": False, "error": str(e)})
                        print(f"  ⚠ 撤退失败：{e}；照常出牌")
                if d.snap:   # 先 Snap 再出牌：失败不影响本回合出牌
                    try:
                        ex.snap()
                        did({"snap": True, "ok": True})
                        print(f"  ⚡ SNAP：赌注 {state.cube_stake} → 下回合 {state.cube_stake * 2}")
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        did({"snap": True, "ok": False, "error": str(e)})
                        print(f"  ⚠ Snap 失败：{e}")
                for a in d.activate:   # 激活先于出牌
                    try:
                        ex.activate(a.card, a.location)
                        ent = next((c.entity for c in state.locations[a.location].my_cards if c.id == a.card), None)
                        activated_cards.add((snap["game_id"], ent))
                        did({"activate": a.card, "location": a.location, "ok": True})
                        print(f"  ✦ 激活 {a.card}（地点{a.location}）")
                        time.sleep(0.5)
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        did({"activate": a.card, "location": a.location, "ok": False, "error": str(e)})
                        print(f"  ⚠ 激活失败：{e}")
                for mv in d.moves:   # 激活 → 移动 → 出牌
                    try:
                        ex.move(mv.card, mv.from_location, mv.to_location)
                        did({"move": mv.card, "from": mv.from_location, "to": mv.to_location, "ok": True})
                        print(f"  ⇄ 移动 {mv.card}：地点{mv.from_location} → 地点{mv.to_location}")
                        time.sleep(0.4)
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        did({"move": mv.card, "ok": False, "error": str(e)})
                        print(f"  ⚠ 移动失败：{e}")
                for a in d.actions:
                    ex.play(a.card, a.location)
                    board.record_play(state.turn, a.location)
                    did({"card": a.card, "location": a.location, "ok": True})
                    time.sleep(0.3)
                ex.end_turn()
                did({"end_turn": True, "ok": True})
                record("done")
                done.add(key)
                played_games.add(snap["game_id"])
                turns += 1
            except GameOver as e:
                record("game_over")
                done.add(key)
                played_games.add(snap["game_id"])
                print(f"  ■ {e}，停止本回合操作")
                continue
            except PlayRejected as e:
                did({"card": e.card_id, "location": e.location, "ok": False, "error": "rejected"})
                if live and (handle_overlay() or guide_active()):
                    record("blocked_by_guide")  # 被提示/指引挡住，不是地点限制：处理后重试
                    print(f"  ↺ {e.card_id} 被新手指引挡住")
                    wait_tutorial()
                    continue
                forbidden.setdefault(key, []).append((e.card_id, e.location))
                record("rejected")
                print(f"  ↺ {e}；记为禁止并重新决策")
                if len(forbidden[key]) >= 6:
                    print("  本回合被拒次数过多，直接结束回合")
                    try:
                        ex.end_turn()
                        done.add(key)
                    except ActionFailed as e2:
                        print(f"  ⚠ {e2}")
                continue
            except ActionFailed as e:
                record(f"failed: {e}")
                failures[key] = failures.get(key, 0) + 1
                print(f"  ⚠ 执行失败（第 {failures[key]} 次）：{e}")
                if failures[key] >= 3:
                    print("  同一回合连续失败 3 次，停止自动模式")
                    stop_reason = "同一回合连续执行失败 3 次"
                    return
                time.sleep(1.5)
                continue
            if not live:
                done.add(key)
            if max_turns and turns >= max_turns:
                print(f"已执行 {turns} 个回合，按 --max-turns 停止")
                stop_reason = f"已执行 {turns} 个回合"
                return
    except ArenaStop as e:
        stop_reason = f"竞技场：{e}"
        print(f"  ■ {stop_reason}")
    except Aborted as e:
        stop_reason = str(e) or "F8 中止"
        print(f"{stop_reason}：已中止")
    except KeyboardInterrupt:
        stop_reason = "Ctrl+C"
    except Exception as e:
        stop_reason = f"异常：{e!r}"
        raise
    finally:
        bus.emit("stopped", reason=stop_reason, games_done=len(finished_games))
        if hasattr(source, "close"):
            source.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["observe", "auto", "clients", "ui"], nargs="?", default="observe")
    ap.add_argument("--client", help="客户端：cn_steam / cn_official / global / auto（覆盖 config [game].client）")
    ap.add_argument("--live", action="store_true", help="auto 模式下真正操作鼠标（默认演练）")
    ap.add_argument("--max-turns", type=int, default=0, help="auto 模式执行 N 个回合后停止（0 = 不限）")
    ap.add_argument("--max-games", type=int, default=0, help="auto 模式打完 N 局后停止（0 = 不限）")
    ap.add_argument("--game-mode", choices=["ranked", "arena"], help="auto 模式：排位（默认）或竞技场（覆盖 config [game].mode）")
    ap.add_argument("--max-runs", type=int, help="竞技场：打完 N 场竞技后停止（覆盖 config [arena].max_runs，0 = 不限）")
    ap.add_argument("--buy-tickets", action=argparse.BooleanOptionalAction, default=None,
                    help="竞技场：门票不足时用金块买（覆盖 config [arena].buy_ticket_with_gold）")
    ap.add_argument("--skip-current", action="store_true", help="auto 模式跳过当前回合，从下一回合开始接管")
    ap.add_argument("--decider", choices=["llm", "rule_based"], help="覆盖 config 中的 brain.decider")
    ap.add_argument("--events", action="store_true", help="auto 模式输出结构化事件、从 stdin 接收控制命令（外部面板使用）")
    ap.add_argument("--port", type=int, default=8765, help="ui 模式：面板端口（只监听 127.0.0.1）")
    ap.add_argument("--no-browser", action="store_true", help="ui 模式：不自动打开浏览器")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    if args.decider:
        settings.setdefault("brain", {})["decider"] = args.decider
    if args.buy_tickets is not None:
        settings.setdefault("arena", {})["buy_ticket_with_gold"] = args.buy_tickets
    if args.mode == "clients":
        list_clients(settings)
        return
    if args.mode == "ui":
        from ui.server.app import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return
    client = select_client(settings, args.client)
    if args.mode == "auto":
        auto(settings, live=args.live, max_turns=args.max_turns, skip_current=args.skip_current, max_games=args.max_games,
             client=client, events=args.events,
             game_mode=args.game_mode or settings.get("game", {}).get("mode", "ranked"), max_runs=args.max_runs)
    else:
        observe(settings, client=client)


if __name__ == "__main__":
    main()

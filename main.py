"""Auto-Snap 入口。

    python main.py observe                      # 观察模式：感知 -> 决策，只打印建议，不操作鼠标
    python main.py auto -v                      # 自动模式演练：打印每个鼠标动作，不动鼠标
    python main.py auto --live --max-turns 1 -v # 实操：只自动打 1 个回合（按住 F8 中止）
    python main.py clients                      # 列出可用客户端及运行状态
    python main.py auto --client cn_official -v # 指定客户端（默认取 config 的 [game].client，可设 SNAP_CLIENT）
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


def auto(settings: dict, live: bool, max_turns: int = 0, skip_current: bool = False, max_games: int = 0,
         client=None) -> None:
    """自动对局：感知 -> 决策 -> 执行 -> 结束回合；对局外按界面识别处理 结算(Collect Rewards) -> 奖励(Next) -> 主界面(Play)。"""
    from brain import rule_based
    from brain.validator import validate
    from executor.actions import ActionFailed, Executor, GameOver, PlayRejected
    from executor.mouse import Aborted
    from perception import make_state_source

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
            m = ex.screen()
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
        from executor.mouse import check_abort

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
            cur = source.snapshot()
            if cur and cur.get("game_id") == game_id and cur.get("finished"):
                return None
    last_menu_check = 0.0
    last_overlay_check = 0.0

    def want_more() -> bool:
        return not max_games or len(finished_games) < max_games

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
                        return m.name
                m = m2
            print("  ⚠ 连点 3 次 PLAY 仍停在主界面")
        return m.name

    def back_to_menu(timeout_s: float = 90) -> None:
        """对局结束后：Collect Rewards -> Next，直到回到主界面（不点 Play）。"""
        end = time.time() + timeout_s
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
    if decider is not None:
        print(f"决策：大模型 {decider.client.provider}/{decider.task.model}（含场地分析）；有回合计时 effort={decider.effort_timed}、"
              f"{decider.deadline_timed_s:.0f}s 截止（T{decider.late_from_turn} 起 {decider.deadline_late_s:.0f}s），无计时 effort={decider.task.effort}、{decider.deadline_s:.0f}s 截止；"
              f"超时由规则 AI 兜底；对局记录 -> {rec.root}")
    else:
        print(f"决策：规则 AI（场地知识库）；对局记录 -> {rec.root}")

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
    if live and want_more() and not skip_current:
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
    if skip_current:  # 当前回合留给玩家手动完成，从下一回合开始接管
        cur = source.snapshot()
        if cur and not cur.get("finished"):
            done.add((cur["game_id"], cur["turn"]))
            print(f"跳过当前回合 T{cur['turn']}，从 T{cur['turn'] + 1} 开始接管")
    try:
        while True:
            state = source.read_state()
            snap = source.last_snapshot
            if snap and not snap.get("finished"):
                played_games.add(snap["game_id"])   # 见过进行中的对局：结束后（含对手 T1 撤退）立即领奖励
            if snap and snap.get("finished") and snap["game_id"] in played_games and snap["game_id"] not in finished_games:
                finished_games.add(snap["game_id"])
                summary = rec.end(snap) or {}
                final_opp = [c.get("id") for l in snap["locations"] for c in l["opp_cards"] if c.get("id")]
                arche = decks.default_kb().record_game(snap["game_id"], opp_seen.get(snap["game_id"], []) + final_opp,
                                                       summary.get("result"))
                if arche:
                    print(f"  对手卡组：{decks.default_kb().opponents[arche].get('name', arche)}（已记入样本）")
                print(f"对局 {snap['game_id'][:8]} 结束：{summary.get('result')}（场地 {summary.get('my_locations_won')}:"
                      f"{summary.get('opp_locations_won')}，赌注 {summary.get('cube_value')}；已完成 {len(finished_games)} 局）")
                if live:
                    back_to_menu()
                if not want_more():
                    print("按 --max-games 停止")
                    return
            if state is None or not snap:
                if decider is not None and hasattr(decider, "warmup"):
                    decider.warmup()           # 等待期间保持到模型服务的连接（新建连接实测 1.4-5s）
                in_game = bool(snap) and not snap.get("finished")
                if live and in_game and time.time() - last_overlay_check > 1:  # 揭示阶段的新卡展示等
                    last_overlay_check = time.time()
                    handle_overlay()
                if live and not in_game and time.time() - last_menu_check > 2:
                    last_menu_check = time.time()
                    try:
                        menu_step(press_play=want_more())
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
            if state.board_notes:
                print("  场上要点：\n    " + "\n    ".join(state.board_notes))
            t_dec = time.time()
            d = decide_watching(state, snap["game_id"])
            if d is None:
                print(f"  ■ 对局已结束（{'对手撤退' if (source.last_snapshot or {}).get('opp', {}).get('conceded') else '结束'}），放弃本回合决策")
                done.add(key)
                continue
            if validate(state, d):
                d = rule_based.decide(state)
            from brain.board import reorder_for_next_card
            if note := reorder_for_next_card(state, d):
                print(f"  ↕ 出牌顺序调整：{note}")
            if d.snap and not snap_enabled:
                d.snap = False
                d.reason += "（Snap 已在 config [brain].snap 关闭）"
            dec_s = time.time() - t_dec
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
            rec.start(snap["game_id"], getattr(source, "deck_name", ""), getattr(source, "deck", []))
            executed: list[dict] = []

            def record(outcome: str) -> None:
                rec.turn(snap["game_id"], state, d, location_report(state), executed, outcome,
                         llm=llm_raw, decide_s=round(dec_s, 2))

            try:
                ex.prepare()
                if d.retreat:
                    try:
                        ex.retreat()
                        executed.append({"retreat": True, "ok": True})
                        record("retreat")
                        done.add(key)
                        played_games.add(snap["game_id"])
                        print(f"  ⤺ 撤退：只输当前赌注 {state.cube_stake}")
                        continue
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        executed.append({"retreat": True, "ok": False, "error": str(e)})
                        print(f"  ⚠ 撤退失败：{e}；照常出牌")
                if d.snap:   # 先 Snap 再出牌：失败不影响本回合出牌
                    try:
                        ex.snap()
                        executed.append({"snap": True, "ok": True})
                        print(f"  ⚡ SNAP：赌注 {state.cube_stake} → 下回合 {state.cube_stake * 2}")
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        executed.append({"snap": True, "ok": False, "error": str(e)})
                        print(f"  ⚠ Snap 失败：{e}")
                for a in d.activate:   # 激活先于出牌
                    try:
                        ex.activate(a.card, a.location)
                        ent = next((c.entity for c in state.locations[a.location].my_cards if c.id == a.card), None)
                        activated_cards.add((snap["game_id"], ent))
                        executed.append({"activate": a.card, "location": a.location, "ok": True})
                        print(f"  ✦ 激活 {a.card}（地点{a.location}）")
                        time.sleep(0.5)
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        executed.append({"activate": a.card, "location": a.location, "ok": False, "error": str(e)})
                        print(f"  ⚠ 激活失败：{e}")
                for mv in d.moves:   # 激活 → 移动 → 出牌
                    try:
                        ex.move(mv.card, mv.from_location, mv.to_location)
                        executed.append({"move": mv.card, "from": mv.from_location, "to": mv.to_location, "ok": True})
                        print(f"  ⇄ 移动 {mv.card}：地点{mv.from_location} → 地点{mv.to_location}")
                        time.sleep(0.4)
                    except GameOver:
                        raise
                    except ActionFailed as e:
                        executed.append({"move": mv.card, "ok": False, "error": str(e)})
                        print(f"  ⚠ 移动失败：{e}")
                for a in d.actions:
                    ex.play(a.card, a.location)
                    executed.append({"card": a.card, "location": a.location, "ok": True})
                    time.sleep(0.3)
                ex.end_turn()
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
                executed.append({"card": e.card_id, "location": e.location, "ok": False, "error": "rejected"})
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
                    return
                time.sleep(1.5)
                continue
            if not live:
                done.add(key)
            if max_turns and turns >= max_turns:
                print(f"已执行 {turns} 个回合，按 --max-turns 停止")
                return
    except Aborted:
        print("F8：已中止")
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(source, "close"):
            source.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["observe", "auto", "clients"], nargs="?", default="observe")
    ap.add_argument("--client", help="客户端：cn_steam / cn_official / global / auto（覆盖 config [game].client）")
    ap.add_argument("--live", action="store_true", help="auto 模式下真正操作鼠标（默认演练）")
    ap.add_argument("--max-turns", type=int, default=0, help="auto 模式执行 N 个回合后停止（0 = 不限）")
    ap.add_argument("--max-games", type=int, default=0, help="auto 模式打完 N 局后停止（0 = 不限）")
    ap.add_argument("--skip-current", action="store_true", help="auto 模式跳过当前回合，从下一回合开始接管")
    ap.add_argument("--decider", choices=["llm", "rule_based"], help="覆盖 config 中的 brain.decider")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    settings = load_settings()
    if args.decider:
        settings.setdefault("brain", {})["decider"] = args.decider
    if args.mode == "clients":
        list_clients(settings)
        return
    client = select_client(settings, args.client)
    if args.mode == "auto":
        auto(settings, live=args.live, max_turns=args.max_turns, skip_current=args.skip_current, max_games=args.max_games,
             client=client)
    else:
        observe(settings, client=client)


if __name__ == "__main__":
    main()

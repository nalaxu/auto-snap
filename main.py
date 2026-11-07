"""Auto-Snap 入口。

    python main.py observe                      # 观察模式：感知 -> 决策，只打印建议，不操作鼠标
    python main.py auto -v                      # 自动模式演练：打印每个鼠标动作，不动鼠标
    python main.py auto --live --max-turns 1 -v # 实操：只自动打 1 个回合（按住 F8 中止）
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


def make_decider(settings: dict):
    """-> (decide 函数, 决策器对象或 None)。decider = "llm" 时用大模型（含场地分析），未配置密钥则退回规则 AI。"""
    from brain import llm, rule_based

    if settings.get("brain", {}).get("decider") == "llm":
        d = llm.make_decider(settings)
        if d is not None:
            return d.decide, d
        print("⚠ brain.decider = llm 但未配置大模型密钥，改用规则 AI")
    return rule_based.decide, None


def observe(settings: dict, interval: float = 0.5) -> None:
    from brain.validator import validate
    from perception import make_state_source

    source = make_state_source(settings)
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


def auto(settings: dict, live: bool, max_turns: int = 0, skip_current: bool = False, max_games: int = 0) -> None:
    """自动对局：感知 -> 决策 -> 执行 -> 结束回合；对局外按界面识别处理 结算(Collect Rewards) -> 奖励(Next) -> 主界面(Play)。"""
    from brain import rule_based
    from brain.validator import validate
    from executor.actions import ActionFailed, Executor, PlayRejected
    from executor.mouse import Aborted
    from perception import make_state_source

    from brain import strategy
    from brain.board import BoardTracker
    from brain.locations import LocationAdvisor, phase
    from gamelog.recorder import GameRecorder

    source = make_state_source(settings)
    advisor = LocationAdvisor()            # 知识库（场地分析已并入大模型逐回合决策）
    strategy.set_default_advisor(advisor)
    rec = GameRecorder()
    board = BoardTracker()                 # 场上要点（消息栈）：每回合重建
    decide, decider = make_decider(settings)
    ex = Executor(source.snapshot, settings.get("game", {}).get("process_name", "SnapCN.exe"), dry_run=not live)
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
    last_menu_check = 0.0
    last_overlay_check = 0.0

    def want_more() -> bool:
        return not max_games or len(finished_games) < max_games

    def menu_step(press_play: bool) -> str | None:
        """对局外：识别界面并点击下一步按钮。返回识别到的界面名。"""
        m = ex.screen()
        if m is None:
            return None
        if m.name in ("collect_rewards", "next"):
            wait_tutorial()
            ex.click_rel(m.center, m.name)
            time.sleep(2.5)
        elif m.name == "play" and press_play:
            wait_tutorial()
            ex.click_rel(m.center, "PLAY")
            end = time.time() + 10
            while time.time() < end:  # 确认已离开主界面（进入匹配）
                time.sleep(1)
                m2 = ex.screen()
                if not m2 or m2.name != "play":
                    print("  ▶ 已点击 PLAY，匹配中…")
                    break
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
    if hasattr(source, "tutorial_running"):  # 预热：定位 TutorialManager（约 10s），避免占用第一回合
        source.tutorial_running()
    if skip_current:  # 当前回合留给玩家手动完成，从下一回合开始接管
        cur = source.snapshot()
        if cur and not cur.get("finished"):
            done.add((cur["game_id"], cur["turn"]))
            print(f"跳过当前回合 T{cur['turn']}，从 T{cur['turn'] + 1} 开始接管")
    try:
        while True:
            state = source.read_state()
            snap = source.last_snapshot
            if snap and snap.get("finished") and snap["game_id"] in played_games and snap["game_id"] not in finished_games:
                finished_games.add(snap["game_id"])
                summary = rec.end(snap) or {}
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
            board.update(snap["game_id"], state)
            if state.board_notes:
                print("  场上要点：\n    " + "\n    ".join(state.board_notes))
            t_dec = time.time()
            d = decide(state)
            if validate(state, d):
                d = rule_based.decide(state)
            dec_s = time.time() - t_dec
            acts = ", ".join(f"{a.card}→地点{a.location}" for a in d.actions) or "不出牌"
            print(f"T{state.turn} 能量{state.energy} [{d.source} {dec_s:.1f}s] {acts}")
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
                for a in d.actions:
                    ex.play(a.card, a.location)
                    executed.append({"card": a.card, "location": a.location, "ok": True})
                    time.sleep(0.3)
                ex.end_turn()
                record("done")
                done.add(key)
                played_games.add(snap["game_id"])
                turns += 1
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
    ap.add_argument("mode", choices=["observe", "auto"], nargs="?", default="observe")
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
    if args.mode == "auto":
        auto(settings, live=args.live, max_turns=args.max_turns, skip_current=args.skip_current, max_games=args.max_games)
    else:
        observe(settings)


if __name__ == "__main__":
    main()

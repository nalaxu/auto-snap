// 事件 -> 页面状态。事件协议见 gamelog/live.py；页面连上 /ws 时先收到 hello（本次运行的全部事件），逐条 reduce 重建状态。

export type CardT = {
  id: string;
  name: string;
  cost: number | null;
  power: number | null;
  ability?: string;
  turn_played?: number;
  activated?: boolean;
  screen_activatable?: boolean;
};

export type LocationT = {
  idx: number;
  id: string;
  name: string;
  effect: string;
  revealed: boolean;
  my_power: number;
  opp_power: number;
  my_cards: CardT[];
  opp_cards: CardT[];
  opp_hidden: number;
  playable: boolean;
  bias?: number | null;
};

export type TurnStateT = {
  turn: number;
  max_turn: number;
  energy: number;
  cube_stake: number;
  cube_next?: number | null;
  can_snap?: boolean;
  opp_snapped?: boolean;
  turn_timer?: boolean;
  timer_left?: number | null;
  timer_total?: number | null;
  hand: CardT[];
  deck_count: number;
  graveyard: number;
  opp_hand?: number | null;
  opp_deck?: number | null;
  locations: LocationT[];
  notes: string[];
  forbidden: [string, number][];
};

export type PlayT = { card: string; name: string; location: number; location_name: string };
export type MoveT = { card: string; name: string; from: number; to: number; from_name?: string; to_name?: string };

export type DecisionT = {
  source: string;
  fallback: boolean;
  skipped: boolean;
  decide_s: number | null;
  actions: PlayT[];
  activate: PlayT[];
  moves: MoveT[];
  snap: boolean;
  retreat: boolean;
  win_prob: number | null;
  win_prob_raw: number | null;
  retreat_why: string;
  reason: string;
  plan: string;
  note: string;
  invalid: string[];
};

export type ExecT = {
  card?: string;
  location?: number;
  activate?: string;
  move?: string;
  from?: number;
  to?: number;
  snap?: boolean;
  retreat?: boolean;
  end_turn?: boolean;
  ok: boolean;
  error?: string;
};

export type TokenT = { name: string; cost: number | null; power: number | null; ability: string };
/** 点名联系：out = 这张牌（或其衍生牌）点名对方；in = 被对方点名。 */
export type CardLinkT = { id: string; name: string; dir: "out" | "in" };
export type ArenaCardT = {
  id: string; name: string; cost: number | null; power: number | null; rating: number | null;
  creates?: TokenT[]; links?: CardLinkT[];
};

export type ArenaT = {
  run_id?: string;
  phase: "none" | "draft" | "run";
  tickets: number;
  score: number;
  hp: number;
  max_hp: number;
  max_score: number;
  games: number;
  swaps_left: number;
  swap_points: number;
  score_per_swap: number;
  next_tier: number | null;
  avatar_progress: number;
  candidates: ArenaCardT[];
  deck: ArenaCardT[];
};

export type SessionT = {
  live: boolean;
  demo?: boolean;
  client: string;
  client_label: string;
  decider: string;
  model: string;
  effort?: string | null;
  max_games: number;
  max_turns: number;
  game_mode?: "ranked" | "arena";
};

export type GameResultT = {
  game_id: string;
  result: "win" | "loss" | "draw" | string;
  retreat?: string | null;
  cube_value?: number | null;
  my_locations_won?: number;
  opp_locations_won?: number;
  archetype?: string | null;
  t: number;
};

export type FeedKind = "play" | "llm" | "state" | "snap" | "warn" | "ok" | "info" | "end";
export type FeedItem = { t: number; turn?: number; kind: FeedKind; text: string };

export type Live = {
  connected: boolean;
  running: boolean;
  pid?: number | null;
  session?: SessionT;
  phase: string;
  paused: boolean;
  stopping: boolean;
  stopReason?: string;
  exitCode?: number | null;
  gameId?: string;
  deckName?: string;
  turn?: TurnStateT;
  turnAt?: number;          // turn_state 收到时刻（本机时钟），用于倒计时
  deadline?: number;
  decision?: DecisionT;
  decidedAt?: number;
  execs: ExecT[];
  turnOutcome?: string;
  results: GameResultT[];
  arena?: ArenaT;
  feed: FeedItem[];
  logs: { t: number; text: string }[];
};

export const initial: Live = {
  connected: false,
  running: false,
  phase: "idle",
  paused: false,
  stopping: false,
  execs: [],
  results: [],
  feed: [],
  logs: [],
};

export type Ev = { kind: string; t?: number; [k: string]: any };

const FEED_MAX = 80;
const LOG_MAX = 400;

function feed(s: Live, item: Omit<FeedItem, "t"> & { t?: number }): FeedItem[] {
  const next = [{ t: item.t ?? Date.now() / 1000, ...item } as FeedItem, ...s.feed];
  return next.slice(0, FEED_MAX);
}

const locName = (s: Live, i?: number) =>
  i == null ? "" : s.turn?.locations[i]?.name || `地点${i}`;

export function describeExec(s: Live, e: ExecT): { kind: FeedKind; text: string } {
  const fail = e.ok ? "" : `失败${e.error ? `：${e.error === "rejected" ? "被游戏拒绝" : e.error}` : ""}`;
  if (e.retreat) return { kind: "warn", text: e.ok ? "撤退：只输当前赌注" : `撤退${fail}` };
  if (e.snap) return { kind: "snap", text: e.ok ? `Snap：赌注 ${s.turn?.cube_stake ?? "?"} → ${(s.turn?.cube_stake ?? 0) * 2}` : `Snap ${fail}` };
  if (e.activate) return { kind: e.ok ? "play" : "warn", text: `激活 ${cardName(s, e.activate)} @ ${locName(s, e.location)} ${fail}` };
  if (e.move) return { kind: e.ok ? "play" : "warn", text: `移动 ${cardName(s, e.move)}：${locName(s, e.from)} → ${locName(s, e.to)} ${fail}` };
  if (e.end_turn) return { kind: "ok", text: "结束回合" };
  if (e.card) return { kind: e.ok ? "play" : "warn", text: `${cardName(s, e.card)} → ${locName(s, e.location)}${e.ok ? "，内存确认落位" : "，" + fail}` };
  return { kind: "info", text: JSON.stringify(e) };
}

export function cardName(s: Live, id: string): string {
  const all = [...(s.turn?.hand ?? []), ...(s.turn?.locations.flatMap((l) => [...l.my_cards, ...l.opp_cards]) ?? [])];
  return all.find((c) => c.id === id)?.name || id;
}

const PHASE_TEXT: Record<string, string> = {
  menu: "回到主界面",
  matching: "已点击 PLAY，匹配中",
  in_game: "进入对局",
  guide: "新手指引：暂停等待玩家点击",
  paused: "已暂停：停在主界面",
};

export function reduce(s: Live, ev: Ev): Live {
  const t = ev.t ?? Date.now() / 1000;
  switch (ev.kind) {
    case "hello": {
      let n: Live = { ...initial, connected: true, running: !!ev.status?.running, pid: ev.status?.pid };
      for (const h of ev.history ?? []) n = reduce(n, h);
      return { ...n, connected: true, running: !!ev.status?.running };
    }
    case "process":
      if (ev.state === "started")
        return { ...initial, connected: s.connected, running: true, pid: ev.pid, phase: "starting", feed: feed(initial, { t, kind: "info", text: "机器人进程已启动" }) };
      return {
        ...s,
        running: false,
        stopping: false,
        exitCode: ev.code,
        phase: "idle",
        feed: feed(s, { t, kind: ev.code === 0 ? "info" : "warn", text: ev.code === 0 ? "机器人进程已退出" : `机器人进程异常退出（代码 ${ev.code}）` }),
      };
    case "session":
      return { ...s, session: ev as unknown as SessionT };
    case "phase": {
      const text = PHASE_TEXT[ev.name];
      return { ...s, phase: ev.name, feed: text ? feed(s, { t, kind: "state", text }) : s.feed };
    }
    case "game_start":
      return {
        ...s,
        gameId: ev.game_id,
        deckName: ev.deck_name,
        turn: undefined,
        decision: undefined,
        execs: [],
        feed: feed(s, { t, kind: "state", text: `新对局 ${String(ev.game_id).slice(0, 8)} · 卡组 ${ev.deck_name || "?"}` }),
      };
    case "turn_state": {
      const st = ev.state as TurnStateT;
      return {
        ...s,
        turn: st,
        turnAt: t,
        deadline: ev.deadline,
        decision: undefined,
        decidedAt: undefined,
        execs: [],
        turnOutcome: undefined,
        feed: feed(s, { t, turn: st.turn, kind: "state", text: `轮到我方：能量 ${st.energy}，手牌 ${st.hand.length} 张，开始决策` }),
      };
    }
    case "decision": {
      const d = ev.decision as DecisionT;
      const src = d.skipped ? "无牌可出，跳过大模型" : d.fallback ? "规则 AI 兜底" : "大模型返回";
      const wp = d.win_prob != null ? ` · 胜率 ${Math.round(d.win_prob * 100)}%` : "";
      return {
        ...s,
        decision: d,
        decidedAt: t,
        feed: feed(s, { t, turn: ev.turn, kind: d.fallback ? "warn" : "llm", text: `${src} ${d.decide_s != null ? d.decide_s.toFixed(1) + "s" : ""}${wp}` }),
      };
    }
    case "exec": {
      const e = ev as unknown as ExecT;
      const execs = [...s.execs, e];
      const n = { ...s, execs };
      const dsc = describeExec(n, e);
      return { ...n, feed: feed(s, { t, turn: ev.turn, ...dsc }) };
    }
    case "turn_done":
      return { ...s, turnOutcome: ev.outcome };
    case "game_end": {
      const r: GameResultT = { ...(ev as any), t };
      const sign = r.result === "win" ? "+" : r.result === "loss" ? "−" : "±";
      const res = r.result === "win" ? "胜" : r.result === "loss" ? "负" : "平";
      return {
        ...s,
        results: [...s.results, r],
        feed: feed(s, { t, kind: "end", text: `对局结束：${res}${r.retreat ? (r.retreat === "opp" ? "（对手撤退）" : "（我方撤退）") : ""} ${sign}${r.cube_value ?? "?"} 魔方${r.archetype ? ` · 对手 ${r.archetype}` : ""}` }),
      };
    }
    case "arena_state":
      return { ...s, arena: ev as unknown as ArenaT };
    case "arena_run_start":
      return { ...s, feed: feed(s, { t, kind: "state", text: `开始新一场竞技（剩余门票 ${ev.tickets ?? "?"}）` }) };
    case "arena_pick":
      return { ...s, feed: feed(s, { t, kind: "llm", text: `组卡 ${ev.round}/12：${(ev.candidates ?? []).join(" / ")} → ${ev.pick}（${ev.reason ?? ""}）` }) };
    case "arena_swap":
      return {
        ...s,
        feed: feed(s, { t, kind: "llm", text: ev.out ? `换牌：${ev.out} → ${ev.inc}（${ev.reason ?? ""}）` : `换牌：候选 ${(ev.candidates ?? []).join(" / ")} 都不换，刷新放弃（${ev.reason ?? ""}）` }),
      };
    case "arena_run_end":
      return { ...s, feed: feed(s, { t, kind: "end", text: `本场竞技结束：积分 ${ev.score}，血量 ${ev.hp}，共 ${ev.games} 局` }) };
    case "control":
      return {
        ...s,
        paused: !!ev.paused,
        stopping: ev.cmd === "stop" ? true : s.stopping,
        feed: feed(s, { t, kind: "info", text: ev.cmd === "pause" ? "收到暂停：本局打完后停在主界面" : ev.cmd === "resume" ? "继续开局" : "收到停止：正在中止" }),
      };
    case "stopped":
      return { ...s, stopReason: ev.reason, feed: feed(s, { t, kind: "info", text: `已停止：${ev.reason}` }) };
    case "log":
      return { ...s, logs: [{ t, text: ev.text }, ...s.logs].slice(0, LOG_MAX) };
    case "__disconnected":
      return { ...s, connected: false };
    default:
      return s;
  }
}

export function sessionRecord(results: GameResultT[]) {
  let w = 0, l = 0, cubes = 0;
  for (const r of results) {
    if (r.result === "win") { w++; cubes += r.cube_value ?? 0; }
    else if (r.result === "loss") { l++; cubes -= r.cube_value ?? 0; }
  }
  return { w, l, cubes };
}

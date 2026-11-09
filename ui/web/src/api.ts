export type ClientOpt = { key: string; label: string; running: boolean; verified: boolean };
export type GameOpt = { file: string; started: string; result: string; cube_value: number; turns_recorded: number; locations: string[] };
export type Options = {
  clients: ClientOpt[]; client: string; decider: string; model: string; games: GameOpt[];
  game_mode?: "ranked" | "arena";
  arena?: { buy_tickets: boolean; max_runs: number; draft_decider: string };
};

export type StartOpts = {
  client?: string;
  live?: boolean;
  decider?: string;
  max_games?: number;
  max_turns?: number;
  skip_current?: boolean;
  game_mode?: "ranked" | "arena";
  max_runs?: number;
  buy_tickets?: boolean;
  demo?: string;
  speed?: number;
  loop?: boolean;
};

export type GameSummary = {
  file: string;
  mode?: "ranked" | "arena";
  arena_hp?: number | null;
  arena_score?: number | null;
  game_id: string;
  started: string;
  ended?: string;
  result: string;
  retreat?: "me" | "opp" | null;
  cube_value: number;
  cubes: number;
  client: string;
  deck: string;
  locations: { id: string; name: string }[];
  my_locations_won?: number;
  opp_locations_won?: number;
  turns: number;
  snap_turn: number | null;
  decide_median: number | null;
  fallbacks: number;
  rejected: number;
  waste: number;
  final_win_prob: number | null;
};

export type Group = { key: string; games: number; wins: number; cubes: number };

export type ArenaRun = {
  run_id: string; started: string; ended: string | null; finished: boolean; score: number | null; hp: number | null;
  games: number; wins: number; losses: number; picks: number; swaps: number; declined_swaps: number; deck: string[];
  tier: number | null;
};

export type ArenaStats = {
  runs: ArenaRun[]; runs_total: number; runs_finished: number; avg_score: number | null;
  tier_dist: [number | null, number][];
  games: number; results: Record<string, number>; cubes: number;
  by_hp: { hp: string; games: number; wins?: number; snaps?: number; retreats?: number; cubes?: number }[];
  forfeits: { run_id: string; hp_before: number; cube_value: number; overflow: number; forfeit: number }[];
  picks: number; swaps: number; declined_swaps: number;
};

export type RatingRow = {
  id: string; name: string; cost: number | null; power: number | null; ability: string;
  rating: number | null; tags: string[]; note: string; source: "auto" | "manual" | null;
  creates?: { name: string; cost: number | null; power: number | null; ability: string }[];
};
export type RatingsResp = { rows: RatingRow[]; matched: number; counts: { total: number; rated: number; manual: number }; tags: string[] };

export type Stats = {
  games: number;
  wins: number;
  losses: number;
  draws: number;
  win_rate: number | null;
  net_cubes: number;
  avg_win: number | null;
  avg_loss: number | null;
  cubes_per_game: number | null;
  streak: { current: number; current_result: string | null; best_win: number };
  trend: { file: string; result: string; cubes: number; total: number; started: string }[];
  cube_dist: { cube: number; win: number; loss: number }[];
  by_client: Group[];
  by_deck: Group[];
  locations: { id: string; name: string; games: number; won?: number; lost?: number; tied?: number; game_wins?: number }[];
  snap: { games: number; wins: number; cubes: number };
  retreat: { me: number; opp: number };
  decisions: {
    total: number; llm: number; fallback: number; skipped: number; fallback_rate: number | null;
    median_s: number | null; p90_s: number | null;
    by_turn: { turn: number; median_s: number | null; p90_s: number | null; n: number }[];
    rejected: number; waste: number; failed: number;
  };
  clients: string[];
};

export type ReviewTurn = {
  turn: number;
  t: string;
  state: import("./store").TurnStateT;
  decision: import("./store").DecisionT;
  executed: import("./store").ExecT[];
  outcome: string;
  flags: string[];
  assessment: { location: number; name: string; effect?: string; favorable_now?: number; why?: string }[];
  targets: number[];
  events: { kind: string; [k: string]: any }[];
};

export type FinalLoc = {
  idx: number; name: string; display: string; my_power: number; opp_power: number;
  my_cards: { id: string; name: string }[]; opp_cards: { id: string; name: string }[];
};

export type GameDetail = { summary: GameSummary; turns: ReviewTurn[]; final: FinalLoc[] | null; trailing_events: any[] };

const qs = (o: Record<string, string | number | undefined>) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== "" && v !== 0);
  return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&") : "";
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {
      /* 非 JSON */
    }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  options: () => call<Options>("/api/options"),
  start: (o: StartOpts) => call("/api/start", { method: "POST", body: JSON.stringify(o) }),
  control: (cmd: "pause" | "resume" | "stop" | "kill") => call("/api/control", { method: "POST", body: JSON.stringify({ cmd }) }),
  stats: (client?: string, last?: number, mode?: string) => call<Stats>("/api/stats" + qs({ client, last, mode })),
  games: (client?: string, last?: number, mode?: string) => call<GameSummary[]>("/api/games" + qs({ client, last, mode })),
  arenaStats: () => call<ArenaStats>("/api/arena/stats"),
  ratings: (q = "", tag = "", source = "") => call<RatingsResp>("/api/arena/ratings" + qs({ q, tag, source })),
  putRating: (id: string, body: { rating: number; tags: string[]; note: string }) =>
    call(`/api/arena/ratings/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  game: (file: string) => call<GameDetail>(`/api/games/${encodeURIComponent(file)}`),
};

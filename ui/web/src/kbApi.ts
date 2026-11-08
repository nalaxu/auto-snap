// 知识库接口（ui/server/kb.py）
export type Signal = {
  kind: "locations" | "cards";
  id: string;
  name: string;
  text: string;
  scope: "here" | "global_here" | "global";
  polarity: "good" | "bad";
  note: string;
  source: string;
  pending: boolean;
};

export type Unmatched = {
  game_id: string;
  date: string;
  result: string;
  cards: string[];
  card_names: Record<string, string>;
  suggest: { id: string; name: string; overlap: number }[];
};

export type UnknownLoc = { id: string; name: string; effect: string; seen: number; last_seen?: string };

export type Overview = {
  pending_signals: Signal[];
  unmatched: Unmatched[];
  unknown_locations: UnknownLoc[];
  counts: { signals: number; unmatched: number; locations: number };
};

export type Opponent = {
  id: string;
  name: string;
  core: string | null;
  signature: string[];
  plan: string;
  counter: string;
  samples: number;
  wins: number;
  seen_cards: [string, number][];
  card_names: Record<string, string>;
};

export type MyDeck = { id: string; name: string; plan: string; cards: Record<string, string>; lessons?: string[]; card_names: Record<string, string> };

export type CardRow = { id: string; name: string; cost: number | null; power: number | null; ability: string; curated: boolean; status?: string };
export type CardDetail = { id: string; name: string; db: Record<string, any> | null; curated: Record<string, any> | null };

export type LocRow = { id: string; name: string; effect: string; curated: boolean; bias?: Bias | null; source?: string | null };
export type Bias = { early: number; mid: number; final: number };
export type LocDetail = {
  id: string;
  name: string;
  db: Record<string, any> | null;
  curated: { effect?: string; bias?: Bias; notes?: string; tags?: string[]; source?: string } | null;
  llm_notes: { game: string; turn: number; favorable_now?: number; why: string }[];
};

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch("/api/kb" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    } catch {
      /* 非 JSON */
    }
    throw new Error(msg);
  }
  return r.json();
}

const enc = encodeURIComponent;
const q = (o: Record<string, string | boolean>) =>
  "?" + Object.entries(o).filter(([, v]) => v !== "" && v !== false).map(([k, v]) => `${k}=${enc(String(v))}`).join("&");

export const kb = {
  overview: () => call<Overview>("GET", "/overview"),
  signals: () => call<Signal[]>("GET", "/signals"),
  putSignal: (kind: string, id: string, b: { scope: string; polarity: string; note: string }) => call("PUT", `/signals/${kind}/${enc(id)}`, b),
  approveSignal: (kind: string, id: string) => call("POST", `/signals/${kind}/${enc(id)}/approve`),
  deleteSignal: (kind: string, id: string) => call("DELETE", `/signals/${kind}/${enc(id)}`),
  assign: (game: string, archetype: string) => call("POST", `/unmatched/${enc(game)}/assign`, { archetype }),
  dismiss: (game: string) => call("DELETE", `/unmatched/${enc(game)}`),
  decks: () => call<{ mine: MyDeck[]; opponents: Opponent[] }>("GET", "/decks"),
  putMine: (id: string, b: { name: string; plan: string; cards: Record<string, string>; lessons?: string[] }) => call("PUT", `/mine/${enc(id)}`, b),
  createOpp: (id: string, b: Partial<Opponent> & { from_game?: string }) => call("POST", `/opponents/${enc(id)}`, b),
  putOpp: (id: string, b: Partial<Opponent>) => call("PUT", `/opponents/${enc(id)}`, b),
  deleteOpp: (id: string) => call("DELETE", `/opponents/${enc(id)}`),
  cards: (query: string, curated = false) => call<CardRow[]>("GET", "/cards" + q({ q: query, curated })),
  card: (id: string) => call<CardDetail>("GET", `/cards/${enc(id)}`),
  putCard: (id: string, b: { cost?: number | null; power?: number | null; ability?: string; notes?: string; tags?: string[] }) => call("PUT", `/cards/${enc(id)}`, b),
  deleteCard: (id: string) => call("DELETE", `/cards/${enc(id)}`),
  locations: (query: string, curated = false) => call<LocRow[]>("GET", "/locations" + q({ q: query, curated })),
  location: (id: string) => call<LocDetail>("GET", `/locations/${enc(id)}`),
  putLocation: (id: string, b: { effect: string; bias: Bias; notes: string; tags: string[] }) => call("PUT", `/locations/${enc(id)}`, b),
  deleteLocation: (id: string) => call("DELETE", `/locations/${enc(id)}`),
};

export const SCOPE_TEXT: Record<string, string> = {
  here: "下一张打到这一路的牌",
  global_here: "我方下一张牌打到这一路",
  global: "我方下一张牌（不限位置）",
};

export type ClientOpt = { key: string; label: string; running: boolean; verified: boolean };
export type GameOpt = { file: string; started: string; result: string; cube_value: number; turns_recorded: number; locations: string[] };
export type Options = { clients: ClientOpt[]; client: string; decider: string; model: string; games: GameOpt[] };

export type StartOpts = {
  client?: string;
  live?: boolean;
  decider?: string;
  max_games?: number;
  max_turns?: number;
  skip_current?: boolean;
  demo?: string;
  speed?: number;
  loop?: boolean;
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
};

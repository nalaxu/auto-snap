// 设置（ui/server/settings.py）与工具（ui/server/tools.py）接口
export type Field = {
  group: string;
  path: string;
  type: "client" | "select" | "bool" | "float" | "int" | "str" | "profile";
  label: string;
  help: string;
  options?: string[] | null;
  optional?: boolean;
  min?: number;
  max?: number;
  value: any;
};
export type Secret = { name: string; used_by: string; set: boolean };
export type SettingsData = { fields: Field[]; secrets: Secret[] };

export type ToolParam = { type: "int"; label: string; default: number; min?: number; max?: number };
export type Tool = {
  id: string;
  label: string;
  icon: string;
  desc: string;
  game: boolean;
  cost?: boolean;
  image?: string;
  image_at?: number | null;
  params?: Record<string, ToolParam>;
};
export type RunView = { id: number; tool: string; running: boolean; code: number | null; started: number; ended: number | null; total: number; lines: string[] };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
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

export const sys = {
  settings: () => call<SettingsData>("GET", "/api/settings"),
  saveSettings: (values: Record<string, any>) => call<{ changed: string[] }>("PUT", "/api/settings", { values }),
  tools: () => call<Tool[]>("GET", "/api/tools"),
  run: (tool: string, params: Record<string, number>) => call<RunView>("POST", `/api/tools/${tool}/run`, { params }),
  runView: (id: number, since: number) => call<RunView>("GET", `/api/tools/runs/${id}?since=${since}`),
  stopRun: (id: number) => call("POST", `/api/tools/runs/${id}/stop`),
};

export const CLIENT_LABEL: Record<string, string> = {
  cn_official: "国服官网",
  cn_steam: "国服 Steam",
  global: "国际服",
  "?": "未记录客户端",
};

export const resultText = (r: string) => (r === "win" ? "胜" : r === "loss" ? "负" : r === "draw" ? "平" : r);

export const signed = (n: number) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : "0");

export const pct = (x: number | null | undefined, digits = 0) => (x == null ? "—" : `${(x * 100).toFixed(digits)}%`);

/** "2026-11-07T22:53:28.350" -> "11-07 22:53" */
export const shortTime = (iso?: string) => (iso ? iso.slice(5, 16).replace("T", " ") : "");

export const FLAG_META: Record<string, { label: string; icon: string; cls: string }> = {
  fallback: { label: "规则 AI 兜底", icon: "ti-robot", cls: "warn" },
  rejected: { label: "出牌被拒", icon: "ti-ban", cls: "bad" },
  failed: { label: "执行失败", icon: "ti-alert-triangle", cls: "bad" },
  waste: { label: "能量没用完", icon: "ti-battery-2", cls: "warn" },
  snap: { label: "Snap", icon: "ti-cube", cls: "gold" },
  retreat: { label: "撤退", icon: "ti-flag", cls: "bad" },
  activate: { label: "激活", icon: "ti-bolt", cls: "info" },
  move: { label: "移动", icon: "ti-arrows-move", cls: "info" },
};

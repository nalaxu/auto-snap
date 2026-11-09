import { useEffect, useState } from "react";
import { api, ArenaStats as ArenaStatsT, GameSummary } from "../api";
import { shortTime, signed } from "../format";
import { GameRow } from "./Stats";

const TIER_TEXT: Record<string, string> = { "15": "神秘变体", "30": "高级神秘变体", "35": "新头像" };

/** 战绩 → 竞技场：整场列表、平均积分、档位分布、按开局血量分组的对局表现、没收观测。 */
export function ArenaStatsView() {
  const [s, setS] = useState<ArenaStatsT | null>(null);
  const [games, setGames] = useState<GameSummary[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    Promise.all([api.arenaStats(), api.games(undefined, 0, "arena")])
      .then(([a, g]) => { setS(a); setGames(g); })
      .catch((e) => setErr(`读取竞技场战绩失败：${e.message}`));
  }, []);
  if (err) return <div className="err">{err}</div>;
  if (!s) return <div className="panel"><div className="empty"><div className="spinner" /><div className="sm">读取竞技场记录…</div></div></div>;
  if (!s.runs_total && !s.games)
    return (
      <div className="panel"><div className="empty"><i className="ti ti-trophy" aria-hidden="true" /><div className="big">还没有竞技场记录</div>
        <div className="sm">在「实时 → 出征准备」选择竞技场打一场后，这里会显示每场积分、档位分布和对局表现</div></div></div>
    );
  const w = s.results.win ?? 0, l = s.results.loss ?? 0;
  const tierMax = Math.max(1, ...s.tier_dist.map(([, n]) => n));
  return (
    <>
      <div className="hero">
        <div className="panel stat"><div className="k">竞技场</div><div className="v">{s.runs_total}<small>场</small></div>
          <div className="s">已结算 {s.runs_finished} 场 · 进行中 {s.runs_total - s.runs_finished}</div></div>
        <div className="panel stat"><div className="k">平均积分</div><div className="v">{s.avg_score ?? "—"}</div>
          <div className="s">按已结算的场次 · 目标：宝箱档位最大</div></div>
        <div className="panel stat"><div className="k">对局</div><div className="v">{s.games}<small>局</small></div>
          <div className="s">{w} 胜 {l} 负 · 魔方 {signed(s.cubes)}</div></div>
        <div className="panel stat"><div className="k">组卡 / 换牌</div><div className="v">{s.picks}<small>/ {s.swaps}</small></div>
          <div className="s">放弃换牌 {s.declined_swaps} 次</div></div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph"><span className="ph-tag">档位分布</span><span className="ph-sub">已结算场次的最终积分档位</span></div>
          <div className="bars">
            {s.tier_dist.length ? s.tier_dist.map(([t, n]) => (
              <div key={String(t)} className="bar">
                <span className="lbl">{t ?? "?"}{TIER_TEXT[String(t)] ? ` ${TIER_TEXT[String(t)]}` : ""}</span>
                <div className="b"><span style={{ width: `${(n / tierMax) * 100}%` }} /></div>
                <span className="n" style={{ color: "var(--gold)" }}>{n}</span>
              </div>
            )) : <div className="brain-empty">还没有结算的场次</div>}
          </div>
        </div>
        <div className="panel">
          <div className="ph"><span className="ph-tag">按开局血量</span><span className="ph-sub">对局开始时的剩余血量 · Snap / 撤退按整场期望调整</span></div>
          <div className="ar-table">
            <div className="ar-trow head"><span>血量</span><span>对局</span><span>胜</span><span>我方 Snap</span><span>我方撤退</span><span>魔方</span></div>
            {s.by_hp.map((r) => (
              <div key={r.hp} className="ar-trow">
                <span>{r.hp === "?" ? "未记录" : r.hp}</span><b>{r.games}</b><b>{r.wins ?? 0}</b><b>{r.snaps ?? 0}</b><b>{r.retreats ?? 0}</b>
                <b style={{ color: (r.cubes ?? 0) >= 0 ? "var(--gold)" : "var(--opp)" }}>{signed(r.cubes ?? 0)}</b>
              </div>
            ))}
          </div>
          <div className="mini-stats">
            <div><b>{s.forfeits.length}</b><span>次血量不足被没收</span>
              <em>{s.forfeits.length ? s.forfeits.map((f) => `超出 ${f.overflow} → 没收 ${f.forfeit}`).join("；") : "用来确认折半时的取整规则"}</em></div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="ph"><span className="ph-tag">每场竞技</span><span className="ph-sub">新的在前</span></div>
        <div className="ar-runs">
          {s.runs.map((r) => (
            <div key={r.run_id} className="ar-run">
              <span>{shortTime(r.started)}</span>
              <span className="sc">{r.score ?? "—"}</span>
              <span>血量 {r.hp ?? "—"}</span>
              <span>{r.wins}-{r.losses}（{r.games} 局）</span>
              <span className="dk" title={r.deck.join(", ")}>{r.deck.length ? r.deck.join(" · ") : `换牌 ${r.swaps} 次`}</span>
              <span className={`st ${r.finished ? "" : "on"}`}>{r.finished ? `${r.tier ?? "?"} 档` : "进行中"}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="ph"><span className="ph-tag">竞技场对局</span><span className="ph-sub">点击进入逐回合复盘</span></div>
        <div className="games">{games.map((g) => <GameRow key={g.file} g={g} />)}</div>
      </div>
    </>
  );
}

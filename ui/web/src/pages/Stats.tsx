import { useEffect, useState } from "react";
import { api, GameSummary, Stats as StatsT } from "../api";
import { CLIENT_LABEL, pct, resultText, shortTime, signed } from "../format";
import { go, reviewHref } from "../route";
import { ArenaStatsView } from "./ArenaStats";

const LASTS: [number, string][] = [[0, "全部"], [50, "近 50 局"], [20, "近 20 局"], [10, "近 10 局"]];

function Trend({ s }: { s: StatsT }) {
  const pts = s.trend;
  if (pts.length < 2) return <div className="brain-empty">至少两局才画走势</div>;
  const W = 720, H = 240, p = 28;
  const vals = pts.map((x) => x.total);
  const max = Math.max(...vals, 0) + 3, min = Math.min(...vals, 0) - 3;
  const x = (i: number) => p + (i * (W - 2 * p)) / (pts.length - 1);
  const y = (v: number) => H - p - ((v - min) * (H - 2 * p)) / (max - min);
  const path = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${path} L${x(pts.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`;
  const step = Math.max(5, Math.ceil((max - min) / 5 / 5) * 5);
  const grid = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) grid.push(v);
  const last = vals[vals.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="魔方累计走势">
      <defs>
        <linearGradient id="ag" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="#ffc63d" stopOpacity=".35" />
          <stop offset="1" stopColor="#ffc63d" stopOpacity="0" />
        </linearGradient>
      </defs>
      {grid.map((v) => (
        <g key={v}>
          <line x1={p} x2={W - p} y1={y(v)} y2={y(v)} stroke="rgba(150,128,255,.12)" strokeDasharray="3 5" />
          <text x={p - 6} y={y(v) + 4} fill="#6f68a3" fontSize="12" textAnchor="end" fontFamily="Teko">{v}</text>
        </g>
      ))}
      <line x1={p} x2={W - p} y1={y(0)} y2={y(0)} stroke="rgba(255,255,255,.25)" />
      <path d={area} fill="url(#ag)" />
      <path d={path} fill="none" stroke="#ffc63d" strokeWidth="2.5" strokeLinejoin="round" />
      {pts.map((g, i) => (
        <circle key={g.file} className="trend-dot" cx={x(i)} cy={y(g.total)} r="4" fill={g.result === "win" ? "#ffc63d" : g.result === "loss" ? "#ff4f72" : "#a8a1d6"}
          stroke="#120f35" strokeWidth="1.5" onClick={() => go(reviewHref(g.file))}>
          <title>{`${shortTime(g.started)} ${resultText(g.result)} ${signed(g.cubes)}，累计 ${signed(g.total)}`}</title>
        </circle>
      ))}
      <text x={x(pts.length - 1) - 8} y={y(last) - 12} fill="#ffc63d" fontFamily="Teko" fontSize="28" textAnchor="end">{signed(last)}</text>
    </svg>
  );
}

function Bar({ label, value, max, neg, right }: { label: string; value: number; max: number; neg?: boolean; right?: string }) {
  return (
    <div className="bar">
      <span className="lbl">{label}</span>
      <div className="b"><span className={neg ? "neg" : ""} style={{ width: `${max ? (value / max) * 100 : 0}%` }} /></div>
      <span className="n" style={{ color: neg ? "var(--opp)" : "var(--gold)" }}>{right ?? value}</span>
    </div>
  );
}

function Latency({ s }: { s: StatsT }) {
  const d = s.decisions;
  const max = Math.max(35, ...d.by_turn.map((t) => t.p90_s ?? 0));
  return (
    <div className="lat">
      {d.by_turn.map((t) => (
        <div key={t.turn} className="lat-col" title={`T${t.turn}：中位 ${t.median_s}s，p90 ${t.p90_s}s（${t.n} 次）`}>
          <div className="lat-bar">
            <span className="p90" style={{ height: `${((t.p90_s ?? 0) / max) * 100}%` }} />
            <span className="med" style={{ height: `${((t.median_s ?? 0) / max) * 100}%` }} />
            <i className="dl" style={{ bottom: `${((t.turn >= 5 ? 35 : 25) / max) * 100}%` }} />
          </div>
          <b>{t.median_s ?? "—"}</b>
          <small>T{t.turn}</small>
        </div>
      ))}
    </div>
  );
}

export function GameRow({ g }: { g: GameSummary }) {
  return (
    <a className="grow" href={reviewHref(g.file)}>
      <span className={`res ${g.result}`}>{resultText(g.result)}</span>
      <span className="when">{shortTime(g.started)}</span>
      <span className="locs">
        {g.locations.map((l) => l.name).join(" · ")}
        {g.mode === "arena" && <em className="mode-tag">竞技场{g.arena_hp != null ? ` · 血 ${g.arena_hp}` : ""}</em>}
        {g.retreat && <em className="tagx">{g.retreat === "opp" ? "对手撤退" : "我方撤退"}</em>}
        {g.snap_turn && <em className="tagx gold">T{g.snap_turn} Snap</em>}
        {g.fallbacks > 0 && <em className="tagx">兜底 {g.fallbacks}</em>}
        {g.rejected > 0 && <em className="tagx bad">被拒 {g.rejected}</em>}
      </span>
      <span className="cb-client">{CLIENT_LABEL[g.client || "?"] ?? g.client}</span>
      <span className={`cb ${g.result}`}>{signed(g.cubes)}</span>
    </a>
  );
}

export function Stats() {
  const [mode, setMode] = useState<"ranked" | "arena">("ranked");
  const modeSeg = (
    <div className="seg sm">
      <button className={mode === "ranked" ? "on" : ""} onClick={() => setMode("ranked")}>排位</button>
      <button className={mode === "arena" ? "on" : ""} onClick={() => setMode("arena")}>竞技场</button>
    </div>
  );
  if (mode === "arena")
    return <div className="stats-page"><div className="filters">{modeSeg}</div><ArenaStatsView /></div>;
  return <RankedStats modeSeg={modeSeg} />;
}

function RankedStats({ modeSeg }: { modeSeg: JSX.Element }) {
  const [client, setClient] = useState("");
  const [last, setLast] = useState(0);
  const [s, setS] = useState<StatsT | null>(null);
  const [games, setGames] = useState<GameSummary[]>([]);
  const [err, setErr] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setErr("");
    Promise.all([api.stats(client, last, "ranked"), api.games(client, last, "ranked")])
      .then(([a, b]) => { setS(a); setGames(b); })
      .catch((e) => setErr(`读取战绩失败：${e.message}`));
  }, [client, last]);

  if (err) return <div className="err">{err}</div>;
  if (!s) return <div className="panel"><div className="empty"><div className="spinner" /><div className="sm">读取对局记录…</div></div></div>;
  if (!s.games)
    return (
      <div className="stats-page"><div className="filters">{modeSeg}</div><div className="panel"><div className="empty"><i className="ti ti-chart-arcs" aria-hidden="true" /><div className="big">还没有对局</div>
        <div className="sm">打完第一局后，这里会显示胜率、魔方走势和决策质量</div></div></div></div>
    );

  const d = s.decisions;
  const distMax = Math.max(...s.cube_dist.flatMap((r) => [r.win, r.loss]), 1);
  const locs = s.locations.slice(0, 12);
  const shown = showAll ? games : games.slice(0, 12);
  return (
    <div className="stats-page">
      <div className="filters">
        {modeSeg}
        <div className="seg sm">
          <button className={!client ? "on" : ""} onClick={() => setClient("")}>全部客户端</button>
          {s.clients.map((c) => (
            <button key={c} className={client === c ? "on" : ""} onClick={() => setClient(c)}>{CLIENT_LABEL[c] ?? c}</button>
          ))}
        </div>
        <div className="seg sm">
          {LASTS.map(([n, t]) => (
            <button key={n} className={last === n ? "on" : ""} onClick={() => setLast(n)}>{t}</button>
          ))}
        </div>
      </div>

      <div className="hero">
        <div className="panel stat"><div className="k">对局</div><div className="v">{s.games}<small>局</small></div>
          <div className="s">{s.wins} 胜 {s.losses} 负{s.draws ? ` ${s.draws} 平` : ""}</div></div>
        <div className="panel stat"><div className="k">胜率</div><div className="v">{s.win_rate != null ? Math.round(s.win_rate * 100) : "—"}<small>%</small></div>
          <div className="s">最长连胜 {s.streak.best_win} · 当前{s.streak.current_result === "win" ? "连胜" : "连败"} {s.streak.current}</div></div>
        <div className="panel stat"><div className="k">魔方净得</div><div className="v" style={{ color: s.net_cubes >= 0 ? "var(--gold)" : "var(--opp)" }}>{signed(s.net_cubes)}</div>
          <div className="s">场均 {s.cubes_per_game != null ? signed(s.cubes_per_game) : "—"} · 目标：期望魔方最大</div></div>
        <div className="panel stat"><div className="k">胜局得 / 负局失</div><div className="v">{s.avg_win ?? "—"}<small>/ {s.avg_loss ?? "—"}</small></div>
          <div className="s">赢的时候要赢得多，输的时候要输得少</div></div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph"><span className="ph-tag">魔方走势</span><span className="ph-sub">累计净得 · 点圆点进入复盘</span></div>
          <div className="chart"><Trend s={s} /></div>
        </div>
        <div className="panel">
          <div className="ph"><span className="ph-tag">输赢分布</span><span className="ph-sub">按结算魔方数</span></div>
          <div className="bars">
            {s.cube_dist.map((r) => (
              <div key={r.cube} className="bar-pair">
                <Bar label={`+${r.cube}`} value={r.win} max={distMax} />
                <Bar label={`−${r.cube}`} value={r.loss} max={distMax} neg />
              </div>
            ))}
          </div>
          <div className="mini-stats">
            <div><b>{s.snap.games}</b><span>局我方 Snap</span><em>{s.snap.wins} 胜 · {signed(s.snap.cubes)}</em></div>
            <div><b>{s.retreat.me}</b><span>次我方撤退</span><em>对手撤退 {s.retreat.opp} 次</em></div>
          </div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="ph"><span className="ph-tag">决策质量</span><span className="ph-sub">{d.total} 次决策 · 柱 = 中位 / p90 耗时，红线 = 截止时间</span></div>
          <div className="dq">
            <div className="dq-nums">
              <div><b>{d.median_s ?? "—"}<small>s</small></b><span>大模型中位耗时</span></div>
              <div><b>{d.p90_s ?? "—"}<small>s</small></b><span>p90 耗时</span></div>
              <div><b className={d.fallback_rate && d.fallback_rate > 0.1 ? "warnc" : ""}>{pct(d.fallback_rate)}</b><span>规则 AI 兜底（{d.fallback} 次）</span></div>
              <div><b className={d.rejected ? "badc" : ""}>{d.rejected}</b><span>出牌被拒</span></div>
              <div><b className={d.waste ? "warnc" : ""}>{d.waste}</b><span>能量没用完的回合</span></div>
            </div>
            <Latency s={s} />
          </div>
        </div>
        <div className="panel">
          <div className="ph"><span className="ph-tag">客户端与卡组</span></div>
          <div className="groups">
            {[...s.by_client.map((g) => ({ ...g, label: CLIENT_LABEL[g.key] ?? g.key, icon: "ti-device-desktop" })),
              ...s.by_deck.map((g) => ({ ...g, label: g.key === "?" ? "未记录卡组" : g.key, icon: "ti-cards" }))].map((g) => (
              <div key={g.icon + g.key} className="grp">
                <i className={`ti ${g.icon}`} aria-hidden="true" />
                <span className="gl">{g.label}</span>
                <span className="gw">{g.wins}-{g.games - g.wins}</span>
                <span className="gr">{Math.round((g.wins / g.games) * 100)}%</span>
                <span className={`gc ${g.cubes >= 0 ? "pos" : "neg"}`}>{signed(g.cubes)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="ph"><span className="ph-tag">场地</span><span className="ph-sub">出现最多的 {locs.length} 个 · 终局时我方在该场地 赢 / 输 / 平</span></div>
        <div className="loc-table">
          {locs.map((l) => {
            const w = l.won ?? 0, lo = l.lost ?? 0, t = l.tied ?? 0;
            return (
              <div key={l.id} className="lt-row">
                <span className="lt-name">{l.name}</span>
                <span className="lt-n">{l.games}</span>
                <div className="lt-bar" title={`赢 ${w} / 输 ${lo} / 平 ${t}`}>
                  <span className="w" style={{ flex: w }} /><span className="t" style={{ flex: t }} /><span className="l" style={{ flex: lo }} />
                </div>
                <span className="lt-wl">{w}/{lo}/{t}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="ph"><span className="ph-tag">对局</span><span className="ph-sub">点击进入逐回合复盘</span></div>
        <div className="games">{shown.map((g) => <GameRow key={g.file} g={g} />)}</div>
        {games.length > 12 && (
          <div className="more"><button className="btn" onClick={() => setShowAll(!showAll)}>{showAll ? "收起" : `显示全部 ${games.length} 局`}</button></div>
        )}
      </div>
    </div>
  );
}

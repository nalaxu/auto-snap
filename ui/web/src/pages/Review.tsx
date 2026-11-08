import { useEffect, useMemo, useState } from "react";
import { api, GameDetail, GameSummary, ReviewTurn } from "../api";
import { Board } from "../components/Board";
import { Brain } from "../components/Brain";
import { Card } from "../components/Card";
import { CLIENT_LABEL, FLAG_META, resultText, shortTime, signed } from "../format";
import { go, reviewHref } from "../route";
import { initial, Live } from "../store";

/** 复盘回合 -> 实时页组件用的状态（复用 Board / Brain）。 */
function asLive(t: ReviewTurn, g: GameDetail): Live {
  return {
    ...initial,
    connected: true,
    turn: t.state,
    turnAt: 1,
    deadline: t.decision.skipped ? 0 : t.turn >= 5 ? 35 : 25,
    decision: t.decision,
    execs: t.executed,
    turnOutcome: t.outcome,
    gameId: g.summary.game_id,
    deckName: g.summary.deck,
    session: { live: true, client: g.summary.client, client_label: CLIENT_LABEL[g.summary.client || "?"] ?? g.summary.client, decider: "llm", model: t.decision.source.replace(/^llm:/, "") || "—", max_games: 0, max_turns: 0 },
  };
}

function Flags({ flags }: { flags: string[] }) {
  return (
    <>
      {flags.map((f) => {
        const m = FLAG_META[f];
        return m ? <span key={f} className={`fl ${m.cls}`} title={m.label}><i className={`ti ${m.icon}`} aria-hidden="true" /></span> : null;
      })}
    </>
  );
}

function Final({ g }: { g: GameDetail }) {
  if (!g.final) return <div className="panel"><div className="brain-empty">这局没有终局记录</div></div>;
  return (
    <div className="panel board">
      <div className="ph"><span className="ph-tag">终局</span><span className="ph-sub">结算时的三路战力与双方场上牌</span></div>
      <div className="lanes">
        {g.final.map((l) => {
          const meLead = l.my_power > l.opp_power, oppLead = l.opp_power > l.my_power;
          const row = (cards: { id: string; name: string }[]) => {
            const out = cards.slice(0, 4).map((c, i) => <Card key={i} card={{ id: c.id, name: c.name, cost: null, power: null }} />);
            while (out.length < 4) out.push(<div key={`s${out.length}`} className="slot" />);
            return <div className="slots">{out}</div>;
          };
          return (
            <div key={l.idx} className="lane">
              {row(l.opp_cards)}
              <div className={`loc ${meLead ? "lead-me" : ""}`} style={{ ["--art" as any]: meLead ? "linear-gradient(135deg,#f5d04a,#e0751f 45%,#5a1a3a)" : oppLead ? "linear-gradient(135deg,#ff6b8a,#9b1d4a 50%,#2a0a3a)" : "linear-gradient(135deg,#6a63a8,#2a2560)" }}>
                <div className="loc-art" />
                <div className="loc-flag"><span className={`flag ${meLead ? "hot" : ""}`}>{meLead ? "我方拿下" : oppLead ? "对手拿下" : "平"}</span></div>
                <div className={`loc-gem opp ${oppLead ? "" : "dim"}`}>{l.opp_power}</div>
                <div className="loc-name">{(l.display || l.name || "?").toUpperCase()}</div>
                <div className={`loc-gem me ${meLead ? "win" : "dim"}`}>{l.my_power}</div>
              </div>
              {row(l.my_cards)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Assessment({ t }: { t: ReviewTurn }) {
  const ex = t.executed;
  const st = t.state;
  const cards = [...st.hand, ...st.locations.flatMap((l) => [...l.my_cards, ...l.opp_cards])];
  const cn = (id?: string) => cards.find((c) => c.id === id)?.name || id || "?";
  const ln = (i?: number) => (i == null ? "?" : st.locations[i]?.name || `地点${i}`);
  return (
    <div className="panel" style={{ marginTop: 18 }}>
      <div className="ph"><span className="ph-tag">回合细节</span><span className="ph-sub">大模型场地评估 · 执行明细</span></div>
      <div className="detail-grid">
        <div>
          {t.assessment.length ? t.assessment.map((a) => (
            <div key={a.location} className={`assess ${t.targets.includes(a.location) ? "target" : ""}`}>
              <div className="as-head">
                <b>{a.name}</b>
                {t.targets.includes(a.location) && <span className="flag hot">争夺目标</span>}
                <span className={`as-score ${(a.favorable_now ?? 0) > 0 ? "pos" : (a.favorable_now ?? 0) < 0 ? "neg" : ""}`}>
                  {a.favorable_now != null ? signed(a.favorable_now) : "—"}
                </span>
              </div>
              {a.effect && <div className="as-eff">{a.effect}</div>}
              {a.why && <div className="as-why">{a.why}</div>}
            </div>
          )) : <div className="brain-empty">这一回合没有大模型场地评估（规则 AI 或跳过）</div>}
        </div>
        <div className="exec-list">
          {ex.length ? ex.map((e, i) => (
            <div key={i} className={`exec ${e.ok ? "" : "bad"}`}>
              <i className={`ti ${e.ok ? "ti-check" : "ti-x"}`} aria-hidden="true" />
              <span>
                {e.snap ? "Snap" : e.retreat ? "撤退" : e.activate ? `激活 ${cn(e.activate)} @ ${ln(e.location)}` : e.move ? `移动 ${cn(e.move)}：${ln(e.from)} → ${ln(e.to)}` : e.card ? `${cn(e.card)} → ${ln(e.location)}` : "?"}
                {e.error && <em> · {e.error === "rejected" ? "被游戏拒绝" : e.error}</em>}
              </span>
            </div>
          )) : <div className="brain-empty">没有执行任何操作</div>}
          <div className={`exec outcome ${t.outcome === "done" ? "" : "bad"}`}>
            <i className="ti ti-flag-3" aria-hidden="true" /><span>回合结果：{t.outcome === "done" ? "正常结束" : t.outcome}</span>
          </div>
          {t.events.map((e, i) => (
            <div key={`e${i}`} className="exec ev"><i className="ti ti-info-circle" aria-hidden="true" /><span>{e.kind}{e.name ? `：${e.name}` : ""}</span></div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function Review({ file }: { file?: string }) {
  const [games, setGames] = useState<GameSummary[]>([]);
  const [g, setG] = useState<GameDetail | null>(null);
  const [idx, setIdx] = useState(0);   // 回合序号；turns.length = 终局
  const [err, setErr] = useState("");

  useEffect(() => {
    api.games().then((list) => {
      setGames(list);
      if (!file && list.length) go(reviewHref(list[0].file));
    }).catch((e) => setErr(`读取对局列表失败：${e.message}`));
  }, []);

  useEffect(() => {
    if (!file) return;
    setG(null);
    setErr("");
    api.game(file).then((d) => { setG(d); setIdx(0); }).catch((e) => setErr(`读取对局失败：${e.message}`));
  }, [file]);

  const n = g?.turns.length ?? 0;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") setIdx((i) => Math.max(0, i - 1));
      if (e.key === "ArrowRight") setIdx((i) => Math.min(n, i + 1));
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [n]);

  const pos = games.findIndex((x) => x.file === file);
  const t = g && idx < n ? g.turns[idx] : null;
  const live = useMemo(() => (t && g ? asLive(t, g) : null), [t, g]);
  const s = g?.summary;

  return (
    <div className="review">
      <aside className="panel rv-list">
        <div className="ph"><span className="ph-tag">对局</span><span className="ph-sub">{games.length} 局</span></div>
        <div className="rv-items">
          {games.map((x) => (
            <a key={x.file} href={reviewHref(x.file)} className={`rv-item ${x.file === file ? "on" : ""}`}>
              <span className={`res ${x.result}`}>{resultText(x.result)}</span>
              <span className="rv-meta"><b>{shortTime(x.started)}</b><small>{x.locations.map((l) => l.name).join(" · ")}</small></span>
              <span className={`cb ${x.result}`}>{signed(x.cubes)}</span>
            </a>
          ))}
        </div>
      </aside>

      <div className="rv-main">
        {err && <div className="err">{err}</div>}
        {!g || !s ? (
          !err && <div className="panel"><div className="empty"><div className="spinner" /><div className="sm">读取对局…</div></div></div>
        ) : (
          <>
            <div className={`panel rv-head ${s.result}`}>
              <div className="rv-result">
                <div className="rr-big">{resultText(s.result)}</div>
                <div className="rr-cubes">{signed(s.cubes)}<small>魔方</small></div>
              </div>
              <div className="rv-info">
                <div className="rv-locs">{s.locations.map((l) => <span key={l.id}>{l.name}</span>)}</div>
                <div className="rv-sub">
                  {shortTime(s.started)} · {CLIENT_LABEL[s.client || "?"] ?? s.client} · 卡组 {s.deck || "未记录"} · 场地 {s.my_locations_won}:{s.opp_locations_won}
                  {s.retreat && ` · ${s.retreat === "opp" ? "对手撤退" : "我方撤退"}`}
                  {s.snap_turn && ` · T${s.snap_turn} Snap`}
                  {s.decide_median != null && ` · 决策中位 ${s.decide_median}s`}
                </div>
              </div>
              <div className="rv-nav">
                <button className="btn" disabled={pos <= 0} onClick={() => go(reviewHref(games[pos - 1].file))} title="更新的一局"><i className="ti ti-chevron-up" aria-hidden="true" /></button>
                <button className="btn" disabled={pos < 0 || pos >= games.length - 1} onClick={() => go(reviewHref(games[pos + 1].file))} title="更早的一局"><i className="ti ti-chevron-down" aria-hidden="true" /></button>
              </div>
            </div>

            <div className="timeline">
              {g.turns.map((x, i) => (
                <button key={i} className={`tl-step ${i === idx ? "on" : ""} ${x.flags.some((f) => FLAG_META[f]?.cls === "bad") ? "bad" : ""}`} onClick={() => setIdx(i)}>
                  <span className="tl-t">T{x.turn}</span>
                  <span className="tl-act">{x.decision.actions.length ? x.decision.actions.map((a) => a.name).join("、") : "不出牌"}</span>
                  <span className="tl-flags"><Flags flags={x.flags} /></span>
                </button>
              ))}
              <button className={`tl-step final ${idx === n ? "on" : ""}`} onClick={() => setIdx(n)}>
                <span className="tl-t">终局</span>
                <span className="tl-act">{s.my_locations_won}:{s.opp_locations_won}</span>
              </button>
              <span className="tl-hint">← → 切换回合</span>
            </div>

            {t && live ? (
              <div className="live">
                <div>
                  <Board s={live} now={1} />
                  <Assessment t={t} />
                </div>
                <Brain s={live} now={1} />
              </div>
            ) : (
              <Final g={g} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

import type { ExecT, Live } from "../store";

const C = 314; // 2πr，r = 50

function Ring({ s, now }: { s: Live; now: number }) {
  const d = s.decision;
  const deadline = s.deadline ?? 0;
  const thinking = !d && !!s.turn && s.turnAt != null;
  const elapsed = d ? d.decide_s ?? 0 : thinking ? now - (s.turnAt ?? now) : 0;
  const frac = deadline > 0 ? Math.min(1, elapsed / deadline) : d ? 1 : 0;
  return (
    <div className={`ring ${thinking ? "thinking" : ""}`}>
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <defs>
          <linearGradient id="rg" x1="0" x2="1">
            <stop offset="0" stopColor="#2aa3ff" />
            <stop offset="1" stopColor="#7ef0ff" />
          </linearGradient>
          <linearGradient id="rgw" x1="0" x2="1">
            <stop offset="0" stopColor="#ff9a1a" />
            <stop offset="1" stopColor="#ff4f72" />
          </linearGradient>
        </defs>
        <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,255,255,.08)" strokeWidth="10" />
        {deadline > 0 && (
          <circle cx="60" cy="60" r="50" fill="none" stroke="rgba(255,79,114,.35)" strokeWidth="10" strokeDasharray={C} strokeDashoffset={C * 0.8} transform="rotate(288 60 60)" />
        )}
        <circle className="ring-arc" cx="60" cy="60" r="50" fill="none" stroke={frac > 0.8 ? "url(#rgw)" : "url(#rg)"} strokeWidth="10" strokeDashoffset={C * (1 - frac)} />
      </svg>
      <div className="val">
        <b>{s.turn ? `${elapsed.toFixed(1)}s` : "—"}</b>
        <small>{deadline > 0 ? `截止 ${deadline.toFixed(0)}s` : s.turn ? "不调用大模型" : "等待回合"}</small>
      </div>
    </div>
  );
}

function headBadge(s: Live) {
  const d = s.decision;
  if (!s.turn) return <span className="badge idle">等待</span>;
  if (!d) return <span className="badge wait">思考中…</span>;
  if (d.skipped) return <span className="badge idle">无牌可出 · 跳过</span>;
  if (d.fallback) return <span className="badge warn">规则 AI 兜底</span>;
  return <span className="badge ok">已返回 · {d.invalid.length ? "已修正" : "合法"}</span>;
}

type Row = { key: string; ic: string; icCls: string; t1: string; t2: string; exec?: ExecT; pending: boolean };

function rows(s: Live): Row[] {
  const d = s.decision;
  if (!d) return [];
  const out: Row[] = [];
  const done = s.execs;
  const find = (pred: (e: ExecT) => boolean) => done.find(pred);
  if (d.retreat) out.push({ key: "r", ic: "ti-flag", icCls: "warn", t1: "撤退", t2: d.retreat_why || "胜率低于撤退线", exec: find((e) => !!e.retreat), pending: true });
  if (d.snap) {
    const st = s.turn;
    out.push({ key: "s", ic: "ti-cube", icCls: "snap", t1: "Snap 加注", t2: st ? `${st.cube_stake} → ${st.cube_stake * 2}` : "", exec: find((e) => !!e.snap), pending: true });
  }
  d.activate.forEach((a, i) => out.push({ key: `a${i}`, ic: "ti-bolt", icCls: "activate", t1: `激活 ${a.name}`, t2: a.location_name, exec: find((e) => e.activate === a.card), pending: true }));
  d.moves.forEach((m, i) => out.push({ key: `m${i}`, ic: "ti-arrows-move", icCls: "move", t1: `${m.name}：${m.from_name ?? m.from} → ${m.to_name ?? m.to}`, t2: "移动", exec: find((e) => e.move === m.card), pending: true }));
  d.actions.forEach((a, i) => {
    const card = s.turn?.hand.find((c) => c.id === a.card);
    out.push({ key: `p${i}`, ic: "ti-cards", icCls: "play", t1: `${a.name} → ${a.location_name}`, t2: card ? `${card.cost} 费 · ${card.power} 战力` : "", exec: find((e) => e.card === a.card && e.location === a.location), pending: true });
  });
  if (!out.length) out.push({ key: "n", ic: "ti-player-skip-forward", icCls: "skip", t1: "本回合不出牌", t2: d.skipped ? "没有能出的牌" : "保留手牌", pending: false });
  return out;
}

function Gauge({ s }: { s: Live }) {
  const d = s.decision;
  const wp = d?.win_prob;
  const raw = d?.win_prob_raw;
  return (
    <div className="panel gauge">
      <div className="ph" style={{ padding: "0 0 10px" }}>
        <span className="ph-tag">胜率</span>
        <span className="sp" />
        <span className="ph-sub">按历史对局校准</span>
      </div>
      <div className="gauge-top">
        <div className="gauge-big">{wp != null ? Math.round(wp * 100) : "—"}<small>{wp != null ? "%" : ""}</small></div>
        <div className="gauge-raw">
          {raw != null && <>模型原始 <s>{Math.round(raw * 100)}%</s><br /></>}
          撤退线约 25%
        </div>
      </div>
      <div className="track">
        <div className="fill" style={{ width: `${wp != null ? wp * 100 : 0}%` }} />
        {raw != null && <div className="raw" style={{ left: `${raw * 100}%` }} />}
        <div className="thr" />
      </div>
      <div className="track-lbl">
        <span style={{ left: "25%", color: "var(--opp)" }}>撤退线</span>
        {wp != null && <span style={{ left: `${wp * 100}%`, color: "var(--gold)" }}>校准</span>}
      </div>
      <div className="verdict">
        {d?.retreat_why ? (
          <><i className="ti ti-scale" aria-hidden="true" />{d.retreat_why}</>
        ) : wp != null ? (
          <><i className="ti ti-shield-check" aria-hidden="true" />{wp >= 0.25 ? "高于撤退线，继续对局" : "低于撤退线"}</>
        ) : (
          <span className="hint">规则 AI 或无牌可出时没有胜率预估</span>
        )}
      </div>
    </div>
  );
}

// 要点的【标签】-> 颜色（标签前缀匹配，如「场地事件·第 6 回合」）
const NOTE_COLORS: [string, string][] = [
  ["本回合有效", "#ffc63d"],
  ["放牌顺序", "#ffc63d"],
  ["可激活", "#7cc8ff"],
  ["可移动", "#38e6a8"],
  ["持续", "#7cc8ff"],
  ["对手上回合", "#ff4f72"],
  ["对手节奏", "#ff8aa2"],
  ["场地事件", "#38e6a8"],
];

function parseNote(n: string): [string, string, string] {
  const m = n.match(/^【([^】]*)】\s*([\s\S]*)$/);
  if (!m) return ["要点", "#a8a1d6", n];
  const color = NOTE_COLORS.find(([p]) => m[1].startsWith(p))?.[1] ?? "#a8a1d6";
  return [m[1], color, m[2]];
}

export function Brain({ s, now }: { s: Live; now: number }) {
  const d = s.decision;
  const list = rows(s);
  const notes = s.turn?.notes ?? [];
  return (
    <aside className="brain">
      <div className="panel">
        <div className="ph">
          <span className="ph-tag">大模型决策</span>
          <span className="sp" />
          {headBadge(s)}
        </div>
        <div className="brain-head">
          <Ring s={s} now={now} />
          <div className="meta">
            <div className="row"><span>来源</span><b>{d ? (d.skipped ? "跳过" : d.fallback ? "规则 AI" : d.source.replace(/^llm:/, "")) : "—"}</b></div>
            <div className="row"><span>模型</span><b>{s.session?.model ?? "—"}</b></div>
            <div className="row"><span>回合</span><b>{s.turn ? `${s.turn.turn} / ${s.turn.max_turn}` : "—"}</b></div>
          </div>
        </div>
        {d?.note && <div className="brain-empty" style={{ paddingTop: 0 }}>{d.note}</div>}
      </div>

      <div className="panel">
        <div className="ph">
          <span className="ph-tag">本回合行动</span>
          <span className="ph-sub">Snap → 激活 → 移动 → 出牌</span>
        </div>
        {list.length ? (
          <div className="actions">
            {list.map((r) => {
              const ok = r.exec?.ok;
              const fail = r.exec && !r.exec.ok;
              return (
                <div key={r.key} className={`act ${fail ? "fail" : ""} ${!r.pending ? "muted" : ""}`}>
                  <div className={`act-ic ${r.icCls}`}><i className={`ti ${r.ic}`} aria-hidden="true" /></div>
                  <div>
                    <div className="t1">{r.t1}</div>
                    <div className="t2">{r.t2}</div>
                  </div>
                  {ok ? (
                    <span className="st"><i className="ti ti-check" aria-hidden="true" /> 已执行</span>
                  ) : fail ? (
                    <span className="st fail" title={r.exec?.error}><i className="ti ti-x" aria-hidden="true" /> 失败</span>
                  ) : r.pending ? (
                    <span className="st wait">待执行</span>
                  ) : (
                    <span className="st wait">—</span>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="brain-empty">{s.turn ? "等大模型给出行动…" : "轮到我方出牌时显示"}</div>
        )}
      </div>

      <Gauge s={s} />

      <div className="panel">
        <div className="ph">
          <span className="ph-tag">关键信息栈</span>
          <span className="ph-sub">每回合重建 · {notes.length} / 10</span>
        </div>
        {notes.length ? (
          <div className="notes">
            {notes.map((n, i) => {
              const [label, color, text] = parseNote(n);
              return (
                <div key={i} className="note" style={{ ["--c" as any]: color }}>
                  <span className="tg">{label}</span>
                  <span title={text}>{text}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="brain-empty">本回合没有需要特别注意的场上要点</div>
        )}
        {(d?.reason || d?.plan) && (
          <div className="reason">
            {d.reason}
            {d.plan && <><br /><span style={{ color: "var(--dim)" }}>规划：{d.plan}</span></>}
          </div>
        )}
      </div>
    </aside>
  );
}

import type { Live, LocationT } from "../store";
import { Card } from "./Card";

const SLOTS = 4;

function Slots({ loc, side, turn }: { loc: LocationT; side: "me" | "opp"; turn: number }) {
  const items = [];
  if (side === "opp") {
    for (const c of loc.opp_cards) items.push(<Card key={`o${items.length}`} card={c} fresh={c.turn_played != null && c.turn_played >= turn - 1} />);
    for (let i = 0; i < loc.opp_hidden; i++) items.push(<Card key={`h${i}`} hidden />);
  } else {
    for (const c of loc.my_cards) items.push(<Card key={`m${items.length}`} card={c} />);
  }
  const shown = items.slice(0, SLOTS);
  while (shown.length < SLOTS) shown.push(<div key={`s${shown.length}`} className="slot" />);
  return <div className="slots">{shown}</div>;
}

const ART = [
  "linear-gradient(135deg,#ff7a3d 0%,#b8222f 45%,#3b0d4a 100%)",
  "linear-gradient(135deg,#3a8dff 0%,#2a3bb8 50%,#140a4a 100%)",
  "linear-gradient(135deg,#f5d04a 0%,#e0751f 45%,#5a1a3a 100%)",
  "linear-gradient(135deg,#38e6a8 0%,#1b7f8f 45%,#0e1a4a 100%)",
  "linear-gradient(135deg,#ff5fb8 0%,#8b2bd6 50%,#1a0a4a 100%)",
  "linear-gradient(135deg,#9fe0ff 0%,#4a6bd6 45%,#1a1050 100%)",
];

function artFor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 33 + id.charCodeAt(i)) >>> 0;
  return ART[h % ART.length];
}

function Lane({ loc, turn, flags }: { loc: LocationT; turn: number; flags: [string, string][] }) {
  const meLead = loc.my_power > loc.opp_power;
  const oppLead = loc.opp_power > loc.my_power;
  return (
    <div className="lane">
      <Slots loc={loc} side="opp" turn={turn} />
      <div className={`loc ${meLead ? "lead-me" : ""} ${loc.revealed ? "" : "unrevealed"}`} style={{ ["--art" as any]: artFor(loc.id || String(loc.idx)) }}>
        <div className="loc-art" />
        <div className="loc-flag">
          {flags.map(([t, c]) => (
            <span key={t} className={`flag ${c}`}>{t}</span>
          ))}
        </div>
        <div className={`loc-gem opp ${oppLead ? "" : "dim"}`}>{loc.opp_power}</div>
        <div className="loc-name">{loc.revealed ? (loc.name || loc.id).toUpperCase() : "未揭示"}</div>
        <div className="loc-eff" title={loc.effect}>{loc.revealed ? loc.effect : "将在之后的回合揭示"}</div>
        <div className={`loc-gem me ${meLead ? "win" : "dim"}`}>{loc.my_power}</div>
      </div>
      <Slots loc={loc} side="me" turn={turn} />
    </div>
  );
}

export function Board({ s, now }: { s: Live; now: number }) {
  const st = s.turn!;
  const d = s.decision;
  // 计划落点：大模型这回合要打的牌 -> 目标地点
  const plans = new Map<string, string>();
  d?.actions.forEach((a) => plans.set(a.card, `→ ${a.location_name}`));
  const flags = (idx: number): [string, string][] => {
    const out: [string, string][] = [];
    if (d?.actions.some((a) => a.location === idx)) out.push(["本回合出牌", "hot"]);
    if (d?.moves.some((m) => m.to === idx)) out.push(["移入", "cold"]);
    if (!st.locations[idx]?.playable) out.push(["不可出牌", ""]);
    return out;
  };

  let left: number | null = null;
  if (st.timer_left != null && s.turnAt) left = Math.max(0, st.timer_left - (now - s.turnAt));
  const pct = left != null && st.timer_total ? Math.min(100, (left / st.timer_total) * 100) : 0;

  const n = st.hand.length;
  return (
    <div className="panel board">
      <div className="ph">
        <span className="ph-tag">战场</span>
        <span className="ph-sub">内存快照 · 第 {st.turn} 回合</span>
        <span className="sp" />
        {left != null && (
          <div className="timer">
            <i className="ti ti-hourglass" aria-hidden="true" />
            回合剩余 <b>{Math.ceil(left)}s</b>
            <div className="timer-bar"><span className={left < 15 ? "low" : ""} style={{ width: `${pct}%` }} /></div>
          </div>
        )}
        <div className="energy">
          <div className="energy-gem">{st.energy}</div>
          <small>本回合<br />能量</small>
        </div>
      </div>
      <div className="lanes">
        {st.locations.map((l) => (
          <Lane key={l.idx} loc={l} turn={st.turn} flags={flags(l.idx)} />
        ))}
      </div>
      <div className="hand-wrap">
        <div className="deckinfo">
          <div><b>{st.deck_count}</b>牌库剩余</div>
          <div><b>{st.graveyard}</b>弃牌堆</div>
        </div>
        <div className="hand">
          {st.hand.map((c, i) => {
            const r = (i - (n - 1) / 2) * 5;
            const y = Math.abs(i - (n - 1) / 2) * 6;
            const plan = plans.get(c.id);
            return (
              <div key={`${c.id}${i}`} className="hand-slot" style={{ transform: `translateY(${y}px)` }}>
                {plan && <span className="plan-tag">{plan}</span>}
                <Card card={c} plan={plan} style={{ transform: `rotate(${r}deg)` }} />
              </div>
            );
          })}
          {n === 0 && <div className="hint">手牌为空</div>}
        </div>
        <div className="opp-hand">
          <div className="backs">
            {Array.from({ length: Math.min(st.opp_hand ?? 0, 7) }).map((_, i) => <span key={i} />)}
          </div>
          <div>对手手牌 {st.opp_hand ?? "?"} · 牌库 {st.opp_deck ?? "?"}</div>
        </div>
      </div>
    </div>
  );
}

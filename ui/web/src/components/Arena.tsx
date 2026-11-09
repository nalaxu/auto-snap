import type { ArenaCardT, ArenaT, Live } from "../store";

const PHASE: Record<ArenaT["phase"], string> = { none: "未开场", draft: "组卡中", run: "整场进行中" };

function Mini({ c, dim }: { c: ArenaCardT; dim?: boolean }) {
  return (
    <div className={`ar-card ${dim ? "dim" : ""}`} title={`${c.name}（${c.id}）${c.rating != null ? ` · 评分 ${c.rating}` : ""}`}>
      <span className="co-s">{c.cost ?? "?"}</span>
      <span className="ar-name">{c.name}</span>
      <span className="pw-s">{c.power ?? "?"}</span>
      {c.rating != null && <span className="ar-rate">{c.rating}</span>}
    </div>
  );
}

/** 竞技场整场状态：血量、积分（离下一档）、换牌进度、本场卡组与下一次换牌候选。 */
export function ArenaPanel({ s }: { s: Live }) {
  const a = s.arena;
  if (!a) return null;
  const tierBase = a.next_tier != null ? a.next_tier - 5 : a.max_score;
  const tierPct = a.next_tier != null ? Math.max(0, Math.min(100, ((a.score - tierBase) / 5) * 100)) : 100;
  const swapPct = Math.min(100, (a.swap_points / Math.max(1, a.score_per_swap)) * 100);
  return (
    <div className="panel arena-live">
      <div className="ph">
        <span className="ph-tag">竞技场</span>
        <span className="ph-sub">{PHASE[a.phase]}{a.run_id ? ` · 本场 ${a.run_id.slice(0, 8)}` : ""} · 门票 {a.tickets} · 头像进度 {a.avatar_progress}/35</span>
      </div>
      <div className="ar-top">
        <div className="ar-block">
          <div className="k">血量</div>
          <div className="ar-hp" aria-label={`血量 ${a.hp}/${a.max_hp}`}>
            {Array.from({ length: a.max_hp }, (_, i) => <i key={i} className={i < a.hp ? "on" : ""} />)}
          </div>
          <div className="s">{a.hp}/{a.max_hp}{a.hp <= 3 && a.phase === "run" ? " · 绝境：Snap 门槛已降低" : ""}</div>
        </div>
        <div className="ar-block">
          <div className="k">积分</div>
          <div className="v">{a.score}<small>/ {a.max_score}</small></div>
          <div className="ar-bar"><span style={{ width: `${tierPct}%` }} /></div>
          <div className="s">{a.next_tier != null ? `离 ${a.next_tier} 档还差 ${a.next_tier - a.score}` : "已到最高档"}</div>
        </div>
        <div className="ar-block">
          <div className="k">换牌</div>
          <div className="v">{a.swaps_left}<small>次可用</small></div>
          <div className="ar-bar swap"><span style={{ width: `${swapPct}%` }} /></div>
          <div className="s">进度 {a.swap_points}/{a.score_per_swap}（赢 1 魔方 1 点 · 掉 1 血 2 点）</div>
        </div>
        <div className="ar-block">
          <div className="k">已打</div>
          <div className="v">{a.games}<small>局</small></div>
          <div className="s">{a.phase === "draft" ? `组卡 ${a.deck.length}/12` : "积分 ≥ 35 或血量归零时结算"}</div>
        </div>
      </div>
      <div className="ar-sec">
        <div className="ar-lbl">{a.phase === "draft" ? `组卡第 ${a.deck.length + 1} 轮候选` : "下一次换牌候选"}</div>
        <div className="ar-row">{a.candidates.length ? a.candidates.map((c, i) => <Mini key={c.id + i} c={c} dim={a.phase === "run" && !a.swaps_left} />) : <span className="ar-empty">—</span>}</div>
      </div>
      <div className="ar-sec">
        <div className="ar-lbl">本场卡组（{a.deck.length}）</div>
        <div className="ar-deck">{a.deck.length ? a.deck.map((c, i) => <Mini key={c.id + i} c={c} />) : <span className="ar-empty">还没有选牌</span>}</div>
      </div>
    </div>
  );
}

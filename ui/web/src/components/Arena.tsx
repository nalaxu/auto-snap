import type { ArenaCardT, ArenaT, CardLinkT, Live } from "../store";

const PHASE: Record<ArenaT["phase"], string> = { none: "未开场", draft: "组卡中", run: "整场进行中" };

const linkText = (l: CardLinkT) => (l.dir === "out" ? `点名 ${l.name}` : `被 ${l.name} 点名`);

function cardTitle(c: ArenaCardT) {
  const lines = [`${c.name}（${c.id}）${c.rating != null ? ` · 评分 ${c.rating}` : ""}`];
  for (const t of c.creates ?? []) lines.push(`衍生 ${t.name} ${t.cost ?? "?"}费${t.power ?? "?"}力：${t.ability}`);
  for (const l of c.links ?? []) lines.push(linkText(l));
  return lines.join("\n");
}

function Mini({ c, dim }: { c: ArenaCardT; dim?: boolean }) {
  const nc = c.creates?.length ?? 0;
  const nl = c.links?.length ?? 0;
  return (
    <div className={`ar-card ${dim ? "dim" : ""} ${nl ? "linked" : ""}`} title={cardTitle(c)}>
      <span className="co-s">{c.cost ?? "?"}</span>
      <span className="ar-name">{c.name}</span>
      <span className="pw-s">{c.power ?? "?"}</span>
      {nc > 0 && <i className="ti ti-sparkles ar-ico" aria-label={`生成 ${nc} 种衍生牌`} />}
      {nl > 0 && <i className="ti ti-link ar-ico" aria-label={`与卡组有 ${nl} 个点名联系`} />}
      {c.rating != null && <span className="ar-rate">{c.rating}</span>}
    </div>
  );
}

/** 衍生牌与点名联系：换牌时看清换下一张牌会带走什么（brain/arena_links.py）。 */
function Relations({ cards, lbl }: { cards: ArenaCardT[]; lbl: string }) {
  const rows = cards.filter((c) => (c.creates?.length ?? 0) + (c.links?.length ?? 0) > 0);
  if (!rows.length) return null;
  return (
    <div className="ar-rel">
      <div className="ar-lbl">{lbl}</div>
      {rows.map((c, i) => (
        <div key={c.id + i} className="ar-rel-row">
          <b>{c.name}</b>
          {(c.creates ?? []).map((t) => (
            <span key={t.name} className="tok" title={t.ability}>
              <i className="ti ti-sparkles" aria-hidden="true" />衍生 {t.name} <small>{t.cost ?? "?"}/{t.power ?? "?"}</small>
              <span className="ab">{t.ability}</span>
            </span>
          ))}
          {(c.links ?? []).map((l) => (
            <span key={l.id + l.dir} className="lk"><i className="ti ti-link" aria-hidden="true" />{linkText(l)}</span>
          ))}
        </div>
      ))}
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
      <Relations cards={a.candidates} lbl={a.phase === "draft" ? "候选：衍生牌与点名联系（选进后）" : "候选：衍生牌与点名联系（换进后）"} />
      <Relations cards={a.deck} lbl="本场卡组：衍生牌与点名联系（换下时一起带走）" />
    </div>
  );
}

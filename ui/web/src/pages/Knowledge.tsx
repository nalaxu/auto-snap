import { useCallback, useEffect, useState } from "react";
import { CardRow, CnDiffResp, kb, LocRow, MyDeck, Opponent, Overview, SCOPE_TEXT, Signal } from "../kbApi";
import { CardEditor, LocationEditor, Modal, OpponentEditor, SignalEditor } from "./kb/editors";
import { Ratings } from "./kb/Ratings";

type Tab = "queue" | "decks" | "signals" | "cards" | "cndiff" | "locations" | "arena";
const TABS: [Tab, string, string][] = [
  ["queue", "待复核", "ti-inbox"],
  ["decks", "卡组", "ti-cards"],
  ["signals", "放牌信号", "ti-arrows-sort"],
  ["cards", "卡牌", "ti-playing-card"],
  ["cndiff", "国服差异", "ti-git-compare"],
  ["locations", "场地", "ti-map-2"],
  ["arena", "竞技场评分", "ti-trophy"],
];

function useToast() {
  const [msg, setMsg] = useState("");
  useEffect(() => {
    if (!msg) return;
    const t = window.setTimeout(() => setMsg(""), 2400);
    return () => window.clearTimeout(t);
  }, [msg]);
  return { msg, show: setMsg };
}

const resultTag = (r: string) => <span className={`res ${r}`}>{r === "win" ? "胜" : r === "loss" ? "负" : r}</span>;

function SignalRow({ s, onEdit, onApprove, onDelete }: { s: Signal; onEdit: () => void; onApprove?: () => void; onDelete: () => void }) {
  return (
    <div className={`kb-row ${s.pending ? "pending" : ""}`}>
      <div className="kb-row-main">
        <div className="kb-row-title">
          <i className={`ti ${s.kind === "cards" ? "ti-playing-card" : "ti-map-2"}`} aria-hidden="true" />
          <b>{s.name}</b>
          <span className={`pol ${s.polarity}`}>{s.polarity === "good" ? "有利" : "不利"}</span>
          <span className="scope">{SCOPE_TEXT[s.scope] ?? s.scope}</span>
          {s.pending && <span className="flag hot">自动识别 · 待复核</span>}
        </div>
        <div className="kb-row-note">{s.note}</div>
        {s.text && s.text !== s.note && <div className="kb-row-text">{s.text}</div>}
        <div className="kb-row-src">来源：{s.source}</div>
      </div>
      <div className="kb-row-acts">
        {onApprove && <button className="btn gold" onClick={onApprove}><i className="ti ti-check" aria-hidden="true" />没问题</button>}
        <button className="btn" onClick={onEdit}><i className="ti ti-edit" aria-hidden="true" />修改</button>
        <button className="icon-btn danger" onClick={onDelete} aria-label="删除"><i className="ti ti-trash" aria-hidden="true" /></button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 待复核
function Queue({ toast, onChanged }: { toast: (m: string) => void; onChanged: () => void }) {
  const [o, setO] = useState<Overview | null>(null);
  const [opps, setOpps] = useState<Opponent[]>([]);
  const [editSig, setEditSig] = useState<Signal | null>(null);
  const [newOpp, setNewOpp] = useState<{ game: string; cards: string[]; names: Record<string, string> } | null>(null);
  const [loc, setLoc] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    kb.overview().then(setO).catch((e) => setErr(e.message));
    kb.decks().then((d) => setOpps(d.opponents)).catch(() => {});
  }, []);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn();
      toast(msg);
      load();
      onChanged();
    } catch (e: any) {
      toast(`失败：${e.message}`);
    }
  };
  if (err) return <div className="err">{err}</div>;
  if (!o) return <div className="brain-empty">读取中…</div>;
  const total = o.counts.signals + o.counts.unmatched + o.counts.locations;
  return (
    <div className="kb-queue">
      {total === 0 && (
        <div className="panel"><div className="empty"><i className="ti ti-circle-check" aria-hidden="true" /><div className="big">都复核完了</div>
          <div className="sm">对局中自动识别的信号、没认出的对手卡组、没收录的场地会出现在这里</div></div></div>
      )}

      {o.unknown_locations.length > 0 && (
        <div className="panel">
          <div className="ph"><span className="ph-tag">未收录场地</span><span className="ph-sub">{o.unknown_locations.length} 个 · 目前只用资料库效果 + 大模型临场判断，补上利弊和要点后更稳</span></div>
          <div className="kb-grid">
            {o.unknown_locations.map((l) => (
              <button key={l.id} className="kb-tile" onClick={() => setLoc(l.id)}>
                <div className="kb-tile-top"><b>{l.name}</b><span>遇到 {l.seen} 次</span></div>
                <div className="kb-tile-eff">{l.effect || "资料库没有效果文字"}</div>
                <div className="kb-tile-cta"><i className="ti ti-pencil-plus" aria-hidden="true" />补充知识</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {o.unmatched.length > 0 && (
        <div className="panel">
          <div className="ph"><span className="ph-tag">没认出的对手卡组</span><span className="ph-sub">{o.unmatched.length} 局 · 值得记录的建成原型，或归入已有原型；不值得的忽略</span></div>
          <div className="kb-list">
            {o.unmatched.map((u) => (
              <div key={u.game_id} className="kb-row">
                <div className="kb-row-main">
                  <div className="kb-row-title">{resultTag(u.result)}<b>对局 {u.game_id}</b><span className="scope">{u.date}</span></div>
                  <div className="cchips">{u.cards.map((c) => <span key={c} className="cchip">{u.card_names[c] || c}</span>)}</div>
                  {u.suggest.length > 0 && <div className="kb-row-src">和已有原型重合：{u.suggest.map((s) => `${s.name}（${s.overlap} 张）`).join("、")}</div>}
                </div>
                <div className="kb-row-acts col">
                  <button className="btn gold" onClick={() => setNewOpp({ game: u.game_id, cards: u.cards, names: u.card_names })}><i className="ti ti-plus" aria-hidden="true" />新建原型</button>
                  <select className="txt sm" value="" onChange={(e) => e.target.value && act(() => kb.assign(u.game_id, e.target.value), "已归入原型")}>
                    <option value="">归入已有原型…</option>
                    {opps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                  <button className="btn" onClick={() => act(() => kb.dismiss(u.game_id), "已忽略")}><i className="ti ti-eye-off" aria-hidden="true" />忽略</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {o.pending_signals.length > 0 && (
        <div className="panel">
          <div className="ph"><span className="ph-tag">自动识别的放牌信号</span><span className="ph-sub">按能力文字自动判断的，确认方向对不对</span></div>
          <div className="kb-list">
            {o.pending_signals.map((s) => (
              <SignalRow key={s.kind + s.id} s={s} onEdit={() => setEditSig(s)} onApprove={() => act(() => kb.approveSignal(s.kind, s.id), "已确认")}
                onDelete={() => confirm(`删除 ${s.name} 的信号？`) && act(() => kb.deleteSignal(s.kind, s.id), "已删除")} />
            ))}
          </div>
        </div>
      )}

      {editSig && <SignalEditor sig={editSig} onClose={() => setEditSig(null)} onSaved={() => { setEditSig(null); toast("信号已保存"); load(); onChanged(); }} />}
      {newOpp && <OpponentEditor opp={{ signature: newOpp.cards }} fromGame={newOpp.game} names={newOpp.names} onClose={() => setNewOpp(null)}
        onSaved={() => { setNewOpp(null); toast("原型已创建"); load(); onChanged(); }} />}
      {loc && <LocationEditor id={loc} onClose={() => setLoc(null)} onSaved={() => { setLoc(null); toast("场地知识已保存"); load(); onChanged(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- 卡组
function MineEditor({ deck, onClose, onSaved }: { deck: MyDeck; onClose: () => void; onSaved: () => void }) {
  const [plan, setPlan] = useState(deck.plan);
  const [cards, setCards] = useState<[string, string][]>(Object.entries(deck.cards));
  const [add, setAdd] = useState("");
  const [err, setErr] = useState("");
  const save = async () => {
    try {
      await kb.putMine(deck.id, { name: deck.name, plan, cards: Object.fromEntries(cards.filter(([k]) => k)), lessons: deck.lessons });
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Modal title={`我方卡组 · ${deck.name}`} onClose={onClose} wide>
      <div className="kb-form">
        <div className="field"><label>整体打法（每回合注入提示词）</label><textarea className="txt" rows={4} value={plan} onChange={(e) => setPlan(e.target.value)} /></div>
        <div className="field"><label>每张牌的要点（只注入当前手牌里的牌）</label>
          <div className="tips">
            {cards.map(([c, tip], i) => (
              <div key={c} className="tip-row">
                <span className="tip-card">{deck.card_names[c] || c}</span>
                <input className="txt" value={tip} onChange={(e) => setCards(cards.map((x, j) => (j === i ? [c, e.target.value] : x)))} />
                <button className="icon-btn danger" onClick={() => setCards(cards.filter((_, j) => j !== i))} aria-label={`移除 ${c}`}><i className="ti ti-x" aria-hidden="true" /></button>
              </div>
            ))}
            <div className="tip-row">
              <input className="txt" value={add} onChange={(e) => setAdd(e.target.value.trim())} placeholder="新增卡牌 id（如 Quinjet）" />
              <button className="btn" onClick={() => { if (add && !cards.some(([c]) => c === add)) setCards([...cards, [add, ""]]); setAdd(""); }}><i className="ti ti-plus" aria-hidden="true" />添加</button>
            </div>
          </div>
        </div>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="row"><button className="btn" onClick={onClose}>取消</button><button className="btn gold" onClick={save}><i className="ti ti-device-floppy" aria-hidden="true" />保存</button></div>
    </Modal>
  );
}

function Decks({ toast }: { toast: (m: string) => void }) {
  const [d, setD] = useState<{ mine: MyDeck[]; opponents: Opponent[] } | null>(null);
  const [mine, setMine] = useState<MyDeck | null>(null);
  const [opp, setOpp] = useState<Opponent | "new" | null>(null);
  const load = useCallback(() => { kb.decks().then(setD); }, []);
  useEffect(load, [load]);
  if (!d) return <div className="brain-empty">读取中…</div>;
  return (
    <div className="kb-queue">
      <div className="panel">
        <div className="ph"><span className="ph-tag">我方卡组</span><span className="ph-sub">卡组名不区分大小写，「loki-tmp」这类临时副本沿用「loki」</span></div>
        <div className="kb-list">
          {d.mine.map((m) => (
            <div key={m.id} className="kb-row">
              <div className="kb-row-main">
                <div className="kb-row-title"><i className="ti ti-cards" aria-hidden="true" /><b>{m.name}</b><span className="scope">{m.id} · {Object.keys(m.cards).length} 张牌有要点</span></div>
                <div className="kb-row-note clamp">{m.plan}</div>
              </div>
              <div className="kb-row-acts"><button className="btn" onClick={() => setMine(m)}><i className="ti ti-edit" aria-hidden="true" />编辑</button></div>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <div className="ph"><span className="ph-tag">对手原型</span><span className="ph-sub">局中看到核心牌或 ≥ 2 张标志牌就注入思路与应对</span><span className="sp" />
          <button className="btn" onClick={() => setOpp("new")}><i className="ti ti-plus" aria-hidden="true" />新建</button></div>
        <div className="kb-list">
          {d.opponents.map((a) => (
            <div key={a.id} className="kb-row">
              <div className="kb-row-main">
                <div className="kb-row-title"><i className="ti ti-skull" aria-hidden="true" /><b>{a.name}</b>
                  <span className="scope">{a.samples} 局样本 · 我方 {a.wins} 胜 {a.samples - a.wins} 负</span></div>
                <div className="cchips">
                  {a.core && <span className="cchip core">{a.card_names[a.core] || a.core}</span>}
                  {a.signature.filter((c) => c !== a.core).map((c) => <span key={c} className="cchip">{a.card_names[c] || c}</span>)}
                </div>
                <div className="kb-row-note clamp"><b>应对：</b>{a.counter}</div>
              </div>
              <div className="kb-row-acts">
                <button className="btn" onClick={() => setOpp(a)}><i className="ti ti-edit" aria-hidden="true" />编辑</button>
                <button className="icon-btn danger" aria-label="删除" onClick={async () => {
                  if (!confirm(`删除原型「${a.name}」？样本也会一起删除（有备份）。`)) return;
                  await kb.deleteOpp(a.id); toast("已删除"); load();
                }}><i className="ti ti-trash" aria-hidden="true" /></button>
              </div>
            </div>
          ))}
        </div>
      </div>
      {mine && <MineEditor deck={mine} onClose={() => setMine(null)} onSaved={() => { setMine(null); toast("卡组已保存"); load(); }} />}
      {opp && <OpponentEditor opp={opp === "new" ? {} : opp} names={opp === "new" ? {} : opp.card_names} onClose={() => setOpp(null)}
        onSaved={() => { setOpp(null); toast("原型已保存"); load(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- 信号
function Signals({ toast, onChanged }: { toast: (m: string) => void; onChanged: () => void }) {
  const [list, setList] = useState<Signal[] | null>(null);
  const [edit, setEdit] = useState<(Partial<Signal> & { kind: Signal["kind"] }) | null>(null);
  const load = useCallback(() => { kb.signals().then(setList); }, []);
  useEffect(load, [load]);
  return (
    <div className="panel">
      <div className="ph"><span className="ph-tag">放牌顺序信号</span><span className="ph-sub">场地 / 卡牌让「下一张牌」受益或受损时，提示本回合先放什么</span><span className="sp" />
        <button className="btn" onClick={() => setEdit({ kind: "locations" })}><i className="ti ti-plus" aria-hidden="true" />场地信号</button>
        <button className="btn" onClick={() => setEdit({ kind: "cards" })}><i className="ti ti-plus" aria-hidden="true" />卡牌信号</button>
      </div>
      <div className="kb-list">
        {!list ? <div className="brain-empty">读取中…</div> : list.map((s) => (
          <SignalRow key={s.kind + s.id} s={s} onEdit={() => setEdit(s)}
            onApprove={s.pending ? async () => { await kb.approveSignal(s.kind, s.id); toast("已确认"); load(); onChanged(); } : undefined}
            onDelete={async () => { if (!confirm(`删除 ${s.name} 的信号？`)) return; await kb.deleteSignal(s.kind, s.id); toast("已删除"); load(); onChanged(); }} />
        ))}
      </div>
      {edit && <SignalEditor sig={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); toast("信号已保存"); load(); onChanged(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- 卡牌 / 场地浏览
function Browse<T extends CardRow | LocRow>({ kind, toast }: { kind: "cards" | "locations"; toast: (m: string) => void }) {
  const [q, setQ] = useState("");
  const [curated, setCurated] = useState(false);
  const [rows, setRows] = useState<T[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => {
    const p = kind === "cards" ? kb.cards(q, curated) : kb.locations(q, curated);
    (p as Promise<T[]>).then(setRows);
  }, [kind, q, curated]);
  useEffect(() => {
    const t = window.setTimeout(load, 200);
    return () => window.clearTimeout(t);
  }, [load]);
  return (
    <div className="panel">
      <div className="ph">
        <span className="ph-tag">{kind === "cards" ? "卡牌" : "场地"}</span>
        <span className="ph-sub">资料库（国服优先，缺的用国际服）+ 人工覆盖（人工优先）· 最多显示 60 条</span>
      </div>
      <div className="kb-search">
        <i className="ti ti-search" aria-hidden="true" />
        <input className="txt" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={kind === "cards" ? "搜名字、id 或能力文字，如 hawkeye / next turn" : "搜名字、id 或效果，如 kun / ongoing"} />
        <label className="check"><input type="checkbox" checked={curated} onChange={(e) => setCurated(e.target.checked)} />只看人工维护的</label>
      </div>
      <div className="kb-table">
        {!rows ? <div className="brain-empty">读取中…</div> : rows.length === 0 ? <div className="brain-empty">没有匹配的结果</div> : rows.map((r) => (
          <button key={r.id} className="kb-trow" onClick={() => setOpen(r.id)}>
            {"cost" in r ? (
              <><span className="kc"><span className="co-s">{r.cost ?? "?"}</span><span className="pw-s">{r.power ?? "?"}</span></span>
                <b>{r.name}</b><span className="kb-tid">{r.id}</span><span className="kb-tdesc">{r.ability || "（白板）"}</span></>
            ) : (
              <><span className="kc">{(r as LocRow).bias ? <span className="bias-mini">{(["early", "mid", "final"] as const).map((k) => { const v = (r as LocRow).bias![k]; return <i key={k} className={v > 0 ? "pos" : v < 0 ? "neg" : ""}>{v > 0 ? `+${v}` : v}</i>; })}</span> : <span className="kb-tid">—</span>}</span>
                <b>{r.name}</b><span className="kb-tid">{r.id}</span><span className="kb-tdesc">{(r as LocRow).effect}</span></>
            )}
            {r.curated ? <span className="flag hot">人工</span> : <span />}
          </button>
        ))}
      </div>
      {open && kind === "cards" && <CardEditor id={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); toast("已保存"); load(); }} />}
      {open && kind === "locations" && <LocationEditor id={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); toast("已保存"); load(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- 国服与国际服差异
const SRC_TEXT: Record<string, string> = { global: "数值取自国际服", unknown: "有数值没读到" };

function CnDiffView({ toast }: { toast: (m: string) => void }) {
  const [d, setD] = useState<CnDiffResp | null>(null);
  const [q, setQ] = useState("");
  const [statsOnly, setStatsOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => { kb.cnDiff().then(setD).catch(() => setD({ meta: null, diff: [] })); }, []);
  useEffect(load, [load]);
  if (!d) return <div className="brain-empty">读取中…</div>;
  const m = d.meta;
  const ql = q.trim().toLowerCase();
  const rows = d.diff.filter((x) => (!statsOnly || x.changes.cost || x.changes.power)
    && (!ql || x.id.toLowerCase().includes(ql) || x.name.toLowerCase().includes(ql) || (x.changes.ability?.[1] || "").toLowerCase().includes(ql)));
  return (
    <div className="panel">
      <div className="ph"><span className="ph-tag">国服与国际服不同的卡</span>
        <span className="ph-sub">国服库由「工具 → 读取国服卡牌库」从游戏内存生成；机器人按 人工覆盖 &gt; 国服 &gt; 国际服 使用</span></div>
      {!m ? (
        <div className="empty"><i className="ti ti-database-off" aria-hidden="true" /><div className="big">还没有国服卡牌库</div>
          <div className="sm">开着国服客户端，到「工具」页运行「读取国服卡牌库」</div></div>
      ) : (
        <>
          <div className="cn-diff-meta">
            <span>客户端版本 {m.client_version || "未知"}</span><span>读取于 {(m.fetched_at || "").replace("T", " ")}</span>
            <span>共 {m.count} 张，不同 {m.diff_count} 张</span>
            {m.params && <span>能力数值：客户端 {m.params.client} · 国际服兜底 {m.params.global} · 未知 {m.params.unknown}</span>}
          </div>
          <div className="kb-search">
            <i className="ti ti-search" aria-hidden="true" />
            <input className="txt" value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜名字、id 或国服能力文字" />
            <label className="check"><input type="checkbox" checked={statsOnly} onChange={(e) => setStatsOnly(e.target.checked)} />只看费用 / 战力变化</label>
          </div>
          <div className="kb-list">
            {rows.length === 0 ? <div className="brain-empty">没有匹配的结果</div> : rows.map((x) => (
              <button key={x.id} className="kb-row cn-diff-row" onClick={() => setOpen(x.id)}>
                <div className="kb-row-main">
                  <div className="kb-row-title"><b>{x.name}</b><span className="kb-tid">{x.id}</span>
                    {x.changes.cost && <span className="chg">费用 <span className="old">{x.changes.cost[0]}</span>→{x.changes.cost[1]}</span>}
                    {x.changes.power && <span className="chg">战力 <span className="old">{x.changes.power[0]}</span>→{x.changes.power[1]}</span>}
                    {x.params_source && SRC_TEXT[x.params_source] && <span className="scope">{SRC_TEXT[x.params_source]}</span>}
                    {x.curated && <span className="flag hot">人工</span>}
                  </div>
                  {x.changes.ability && (<>
                    <div className="ab"><span className="lbl">国服</span>{x.changes.ability[1] || "（白板）"}</div>
                    <div className="ab old"><span className="lbl">国际服</span>{x.changes.ability[0] || "（白板）"}</div>
                  </>)}
                </div>
              </button>
            ))}
          </div>
        </>
      )}
      {open && <CardEditor id={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); toast("已保存"); load(); }} />}
    </div>
  );
}

export function Knowledge({ tab, running, onChanged }: { tab?: string; running: boolean; onChanged: () => void }) {
  const cur = (TABS.find(([t]) => t === tab)?.[0] ?? "queue") as Tab;
  const [counts, setCounts] = useState<Overview["counts"] | null>(null);
  const toast = useToast();
  const refresh = useCallback(() => {
    kb.overview().then((o) => setCounts(o.counts)).catch(() => {});
    onChanged();
  }, [onChanged]);
  useEffect(refresh, [refresh]);
  const pending = counts ? counts.signals + counts.unmatched + counts.locations : 0;
  return (
    <div className="kb-page">
      <div className="kb-tabs">
        {TABS.map(([t, label, icon]) => (
          <a key={t} href={`#/kb/${t}`} className={cur === t ? "on" : ""}>
            <i className={`ti ${icon}`} aria-hidden="true" />{label}
            {t === "queue" && pending > 0 && <span className="count">{pending}</span>}
          </a>
        ))}
        <span className="sp" />
        {running && <span className="kb-warn"><i className="ti ti-alert-circle" aria-hidden="true" />机器人运行中：修改已写入文件，运行中的机器人下次启动才会读到</span>}
      </div>
      {cur === "queue" && <Queue toast={toast.show} onChanged={refresh} />}
      {cur === "decks" && <Decks toast={toast.show} />}
      {cur === "signals" && <Signals toast={toast.show} onChanged={refresh} />}
      {cur === "cards" && <Browse kind="cards" toast={toast.show} />}
      {cur === "cndiff" && <CnDiffView toast={toast.show} />}
      {cur === "locations" && <Browse kind="locations" toast={toast.show} />}
      {cur === "arena" && <Ratings toast={toast.show} />}
      {toast.msg && <div className="toast">{toast.msg}</div>}
    </div>
  );
}

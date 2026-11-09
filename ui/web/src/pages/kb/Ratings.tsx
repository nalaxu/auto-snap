import { useCallback, useEffect, useState } from "react";
import { api, RatingRow, RatingsResp } from "../../api";
import { Modal } from "./editors";

const TAG_TEXT: Record<string, string> = { standalone: "独立成立", tech: "针对卡" };
const tagLabel = (t: string) => TAG_TEXT[t] ?? t.replace("needs:", "需要 ").replace("enabler:", "提供 ");
const tagCls = (t: string) => (t.startsWith("needs:") ? "need" : t.startsWith("enabler:") ? "en" : "");

function RatingEditor({ row, tags, onClose, onSaved }: { row: RatingRow; tags: string[]; onClose: () => void; onSaved: () => void }) {
  const [rating, setRating] = useState(row.rating ?? 5);
  const [sel, setSel] = useState<string[]>(row.tags);
  const [note, setNote] = useState(row.note);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const toggle = (t: string) => setSel((s) => (s.includes(t) ? s.filter((x) => x !== t) : [...s, t]));
  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      await api.putRating(row.id, { rating, tags: sel, note });
      onSaved();
    } catch (e: any) {
      setErr(`保存失败：${e.message}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`${row.name}（${row.id}）`} onClose={onClose} wide>
      <div className="rt-edit">
        <div className="ab">{row.cost ?? "?"} 费 {row.power ?? "?"} 力 · {row.ability || "（白板）"}</div>
        {(row.creates ?? []).map((t) => (
          <div key={t.name} className="ab rt-tok"><i className="ti ti-sparkles" aria-hidden="true" /> 衍生 {t.name}（{t.cost ?? "?"} 费 {t.power ?? "?"} 力）<span className="t">· {t.ability}</span></div>
        ))}
        <div className="field">
          <label>竞技场评分：{rating}（1–10，随机组卡环境下的期望强度）</label>
          <input type="range" min={1} max={10} step={0.5} value={rating} onChange={(e) => setRating(Number(e.target.value))} />
        </div>
        <div className="field">
          <label>标签（需要 = 有组件才成立，提供 = 给该体系当组件）</label>
          <div className="rt-taglist">
            {tags.map((t) => <button key={t} className={sel.includes(t) ? "on" : ""} onClick={() => toggle(t)}>{tagLabel(t)}</button>)}
          </div>
        </div>
        <div className="field">
          <label>要点（≤ 20 字，组卡 / 对局时注入给大模型）</label>
          <input className="txt" value={note} maxLength={60} onChange={(e) => setNote(e.target.value)} />
        </div>
        {err && <div className="err">{err}</div>}
        <div className="row" style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn gold" disabled={busy} onClick={save}><i className="ti ti-check" aria-hidden="true" />保存为人工复核</button>
        </div>
      </div>
    </Modal>
  );
}

/** 知识库 → 竞技场评分：大模型初值（auto）逐张复核，保存后标为人工（manual），重新生成不会覆盖。 */
export function Ratings({ toast }: { toast: (m: string) => void }) {
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [source, setSource] = useState("");
  const [d, setD] = useState<RatingsResp | null>(null);
  const [open, setOpen] = useState<RatingRow | null>(null);
  const load = useCallback(() => { api.ratings(q, tag, source).then(setD).catch(() => setD(null)); }, [q, tag, source]);
  useEffect(() => { const t = window.setTimeout(load, 200); return () => window.clearTimeout(t); }, [load]);
  return (
    <div className="panel">
      <div className="ph">
        <span className="ph-tag">竞技场评分</span>
        <span className="ph-sub">
          {d ? `卡池 ${d.counts.total} 张 · 已评分 ${d.counts.rated} · 人工复核 ${d.counts.manual} · 匹配 ${d.matched}（最多显示 120）` : "读取中…"}
          {" "}· 组卡 / 换牌的单卡强度，按评分降序
        </span>
      </div>
      <div className="kb-search">
        <i className="ti ti-search" aria-hidden="true" />
        <input className="txt" value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜名字、id、能力或要点" />
        <select className="txt" style={{ maxWidth: 170 }} value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">全部标签</option>
          {d?.tags.map((t) => <option key={t} value={t}>{tagLabel(t)}</option>)}
        </select>
        <select className="txt" style={{ maxWidth: 140 }} value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">全部来源</option>
          <option value="auto">待复核（大模型）</option>
          <option value="manual">人工复核</option>
          <option value="missing">未评分</option>
        </select>
      </div>
      <div className="kb-table">
        {!d ? <div className="brain-empty">读取中…</div> : d.rows.length === 0 ? <div className="brain-empty">没有匹配的结果</div> : d.rows.map((r) => (
          <button key={r.id} className="rt-row" onClick={() => setOpen(r)}>
            <span className={`rt-score ${r.rating == null ? "none" : ""}`}>{r.rating ?? "—"}</span>
            <b title={r.id}>{r.name}</b>
            <span className="kc"><span className="co-s">{r.cost ?? "?"}</span><span className="pw-s">{r.power ?? "?"}</span></span>
            <span className="rt-desc">
              <span className="nt">{r.note || <span style={{ color: "var(--mute)" }}>（没有要点）</span>}</span>
              <span className="rt-tags">{r.tags.map((t) => <em key={t} className={tagCls(t)}>{tagLabel(t)}</em>)}</span>
              <span className="ab">{r.ability || "（白板）"}</span>
              {(r.creates ?? []).map((t) => (
                <span key={t.name} className="rt-tok" title={t.ability}><i className="ti ti-sparkles" aria-hidden="true" /> 衍生 {t.name} {t.cost ?? "?"}/{t.power ?? "?"}<span className="t"> · {t.ability}</span></span>
              ))}
            </span>
            {r.source === "manual" ? <span className="flag hot">人工</span> : <span />}
          </button>
        ))}
      </div>
      {open && d && <RatingEditor row={open} tags={d.tags} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); toast("已保存"); load(); }} />}
    </div>
  );
}

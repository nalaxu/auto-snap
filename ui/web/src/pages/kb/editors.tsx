import { ReactNode, useEffect, useState } from "react";
import { Bias, CardDetail, kb, LocDetail, Opponent, SCOPE_TEXT, Signal } from "../../kbApi";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className={`panel modal kb-modal ${wide ? "wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="kb-modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="关闭"><i className="ti ti-x" aria-hidden="true" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** 保存按钮 + 错误提示的通用尾部。 */
function Foot({ busy, err, onSave, onCancel, saveText = "保存", extra }: { busy: boolean; err: string; onSave: () => void; onCancel: () => void; saveText?: string; extra?: ReactNode }) {
  return (
    <>
      {err && <div className="err">{err}</div>}
      <div className="row">
        {extra}
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onCancel}>取消</button>
        <button className="btn gold" disabled={busy} onClick={onSave}><i className="ti ti-device-floppy" aria-hidden="true" />{saveText}</button>
      </div>
    </>
  );
}

function useSave(onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr("");
    try {
      await fn();
      onDone();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, err, run };
}

// ---------------------------------------------------------------- 信号
export function SignalEditor({ sig, onClose, onSaved }: { sig: Partial<Signal> & { kind: Signal["kind"] }; onClose: () => void; onSaved: () => void }) {
  const [id, setId] = useState(sig.id ?? "");
  const [scope, setScope] = useState(sig.scope ?? "here");
  const [polarity, setPolarity] = useState(sig.polarity ?? "good");
  const [note, setNote] = useState(sig.note ?? "");
  const s = useSave(onSaved);
  const isNew = !sig.id;
  return (
    <Modal title={isNew ? "新增放牌顺序信号" : `编辑信号 · ${sig.name}`} onClose={onClose}>
      {sig.text && <div className="kb-quote">{sig.text}</div>}
      <div className="kb-form">
        {isNew && (
          <div className="field"><label>{sig.kind === "cards" ? "卡牌 id（CardDefId）" : "场地 id（LocationDefId）"}</label>
            <input className="txt" value={id} onChange={(e) => setId(e.target.value.trim())} placeholder={sig.kind === "cards" ? "Shuri" : "CastleZemo"} /></div>
        )}
        <div className="field"><label>影响谁</label>
          <div className="seg">
            {Object.entries(SCOPE_TEXT).map(([k, t]) => (
              <button key={k} className={scope === k ? "on" : ""} onClick={() => setScope(k as Signal["scope"])}>{t}</button>
            ))}
          </div>
        </div>
        <div className="field"><label>对我方</label>
          <div className="seg">
            <button className={polarity === "good" ? "on" : ""} onClick={() => setPolarity("good")}>有利：先放想加强的牌</button>
            <button className={polarity === "bad" ? "on danger" : ""} onClick={() => setPolarity("bad")}>不利：先放不怕的牌 / 避开</button>
          </div>
        </div>
        <div className="field"><label>说明（会注入关键信息栈）</label>
          <textarea className="txt" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="下一张打到这里的牌换到对手一侧" /></div>
      </div>
      <Foot busy={s.busy} err={s.err} onCancel={onClose} onSave={() => s.run(() => kb.putSignal(sig.kind, id, { scope, polarity, note }))} />
    </Modal>
  );
}

// ---------------------------------------------------------------- 卡牌选择（逗号分隔 id，带名字提示）
function CardChips({ ids, names, onChange }: { ids: string[]; names: Record<string, string>; onChange: (ids: string[]) => void }) {
  const [add, setAdd] = useState("");
  return (
    <div className="chips-edit">
      {ids.map((c) => (
        <span key={c} className="cchip">{names[c] || c}<button onClick={() => onChange(ids.filter((x) => x !== c))} aria-label={`移除 ${c}`}>×</button></span>
      ))}
      <input className="txt sm" value={add} placeholder="+ 卡牌 id，回车添加" onChange={(e) => setAdd(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && add.trim()) {
            if (!ids.includes(add.trim())) onChange([...ids, add.trim()]);
            setAdd("");
          }
        }} />
    </div>
  );
}

// ---------------------------------------------------------------- 对手原型
export function OpponentEditor({ opp, fromGame, names, onClose, onSaved }: {
  opp: Partial<Opponent>; fromGame?: string; names: Record<string, string>; onClose: () => void; onSaved: () => void;
}) {
  const isNew = !opp.id;
  const [id, setId] = useState(opp.id ?? "");
  const [name, setName] = useState(opp.name ?? "");
  const [core, setCore] = useState(opp.core ?? "");
  const [sig, setSig] = useState<string[]>(opp.signature ?? []);
  const [plan, setPlan] = useState(opp.plan ?? "");
  const [counter, setCounter] = useState(opp.counter ?? "");
  const s = useSave(onSaved);
  const body = { name, core: core || null, signature: sig, plan, counter };
  return (
    <Modal title={isNew ? (fromGame ? `从对局 ${fromGame} 新建对手原型` : "新建对手原型") : `编辑原型 · ${opp.name}`} onClose={onClose} wide>
      <div className="kb-form two">
        {isNew && <div className="field"><label>原型 id（英文，用于索引）</label><input className="txt" value={id} onChange={(e) => setId(e.target.value.trim())} placeholder="bounce" /></div>}
        <div className="field"><label>名字</label><input className="txt" value={name} onChange={(e) => setName(e.target.value)} placeholder="弹回（Beast / Falcon + 低费 On Reveal）" /></div>
        <div className="field"><label>核心牌（看到一张就认定）</label>
          <select className="txt" value={core} onChange={(e) => setCore(e.target.value)}>
            <option value="">（无）</option>
            {sig.map((c) => <option key={c} value={c}>{names[c] || c}</option>)}
          </select>
        </div>
        <div className="field full"><label>标志牌（看到 ≥ 2 张认定为这个原型）</label><CardChips ids={sig} names={names} onChange={setSig} /></div>
        <div className="field full"><label>对手思路</label><textarea className="txt" rows={3} value={plan} onChange={(e) => setPlan(e.target.value)} /></div>
        <div className="field full"><label>我方应对（会注入提示词）</label><textarea className="txt" rows={3} value={counter} onChange={(e) => setCounter(e.target.value)} /></div>
      </div>
      <Foot busy={s.busy} err={s.err} onCancel={onClose} saveText={isNew ? "创建" : "保存"}
        onSave={() => s.run(() => (isNew ? kb.createOpp(id, { ...body, from_game: fromGame }) : kb.putOpp(opp.id!, body)))} />
    </Modal>
  );
}

// ---------------------------------------------------------------- 场地
const PHASES: [keyof Bias, string][] = [["early", "前期 T1–3"], ["mid", "中期 T4–5"], ["final", "最后回合"]];

export function LocationEditor({ id, onClose, onSaved }: { id: string; onClose: () => void; onSaved: () => void }) {
  const [d, setD] = useState<LocDetail | null>(null);
  const [effect, setEffect] = useState("");
  const [bias, setBias] = useState<Bias>({ early: 0, mid: 0, final: 0 });
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("");
  const s = useSave(onSaved);
  const del = useSave(onSaved);
  useEffect(() => {
    kb.location(id).then((x) => {
      setD(x);
      const c = x.curated;
      setEffect(c?.effect || x.db?.effect || "");
      setBias({ early: 0, mid: 0, final: 0, ...(c?.bias || {}) });
      setNotes(c?.notes || "");
      setTags((c?.tags || []).join(", "));
    });
  }, [id]);
  return (
    <Modal title={`场地知识 · ${d?.name ?? id}`} onClose={onClose} wide>
      {!d ? <div className="brain-empty">读取中…</div> : (
        <div className="kb-split">
          <div className="kb-form">
            <div className="field"><label>效果（国服与资料库不同时改这里）</label><textarea className="txt" rows={2} value={effect} onChange={(e) => setEffect(e.target.value)} /></div>
            <div className="field"><label>往这里放牌的利弊（−3 很不利 … +3 很有利）</label>
              <div className="bias-grid">
                {PHASES.map(([k, t]) => (
                  <div key={k} className="bias-row">
                    <span>{t}</span>
                    <input type="range" min={-3} max={3} step={1} value={bias[k]} onChange={(e) => setBias({ ...bias, [k]: Number(e.target.value) })} />
                    <b className={bias[k] > 0 ? "pos" : bias[k] < 0 ? "neg" : ""}>{bias[k] > 0 ? `+${bias[k]}` : bias[k]}</b>
                  </div>
                ))}
              </div>
            </div>
            <div className="field"><label>打法要点</label><textarea className="txt" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="越早放越赚；Ongoing 牌放这里翻倍" /></div>
            <div className="field"><label>标签（逗号分隔，可选）</label><input className="txt" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="buff_per_turn, ongoing_double" /></div>
          </div>
          <div className="kb-ref">
            <div className="kb-ref-t">资料库原文</div>
            <div className="kb-quote">{d.db?.effect || "资料库没有这个场地"}</div>
            {d.curated?.source && <div className="hint">当前人工条目来源：{d.curated.source}</div>}
            <div className="kb-ref-t">大模型在对局中的判断</div>
            {d.llm_notes.length ? d.llm_notes.map((n, i) => (
              <div key={i} className="llm-note">
                <span className={`as-score ${(n.favorable_now ?? 0) > 0 ? "pos" : (n.favorable_now ?? 0) < 0 ? "neg" : ""}`}>{n.favorable_now != null ? (n.favorable_now > 0 ? `+${n.favorable_now}` : n.favorable_now) : "—"}</span>
                <span>{n.why}<em> · {n.game} T{n.turn}</em></span>
              </div>
            )) : <div className="hint">还没有对局记录到这个场地</div>}
          </div>
        </div>
      )}
      <Foot busy={s.busy || del.busy} err={s.err || del.err} onCancel={onClose}
        extra={d?.curated ? <button className="btn danger-ghost" onClick={() => confirm("删除这个场地的人工条目？之后回落到资料库效果。") && del.run(() => kb.deleteLocation(id))}><i className="ti ti-trash" aria-hidden="true" />删除人工条目</button> : undefined}
        onSave={() => s.run(() => kb.putLocation(id, { effect, bias, notes, tags: tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean) }))} />
    </Modal>
  );
}

// ---------------------------------------------------------------- 卡牌
export function CardEditor({ id, onClose, onSaved }: { id: string; onClose: () => void; onSaved: () => void }) {
  const [d, setD] = useState<CardDetail | null>(null);
  const [cost, setCost] = useState("");
  const [power, setPower] = useState("");
  const [ability, setAbility] = useState("");
  const [notes, setNotes] = useState("");
  const s = useSave(onSaved);
  const del = useSave(onSaved);
  useEffect(() => {
    kb.card(id).then((x) => {
      setD(x);
      const c = x.curated || {};
      setCost(c.cost != null ? String(c.cost) : "");
      setPower(c.power != null ? String(c.power) : "");
      setAbility(c.ability || "");
      setNotes(c.notes || "");
    });
  }, [id]);
  const num = (v: string) => (v.trim() === "" ? null : Number(v));
  return (
    <Modal title={`卡牌 · ${d?.name ?? id}`} onClose={onClose} wide>
      {!d ? <div className="brain-empty">读取中…</div> : (
        <div className="kb-split">
          <div className="kb-form">
            <div className="hint">只填和资料库不同的项（国服平衡改动、实测结果）；留空 = 用资料库的值。</div>
            <div className="kb-form two">
              <div className="field"><label>费用（资料库 {d.db?.cost ?? "—"}）</label><input className="txt" type="number" value={cost} onChange={(e) => setCost(e.target.value)} /></div>
              <div className="field"><label>战力（资料库 {d.db?.power ?? "—"}）</label><input className="txt" type="number" value={power} onChange={(e) => setPower(e.target.value)} /></div>
            </div>
            <div className="field"><label>能力（国服文字不同时填）</label><textarea className="txt" rows={3} value={ability} onChange={(e) => setAbility(e.target.value)} /></div>
            <div className="field"><label>备注（实测发现、打法要点）</label><textarea className="txt" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
          </div>
          <div className="kb-ref">
            <div className="kb-ref-t">资料库</div>
            <div className="kb-quote">
              <b>{d.db?.cost ?? "?"} 费 / {d.db?.power ?? "?"} 战力</b>{d.db?.series ? ` · ${d.db.series}` : ""}<br />
              {d.db?.ability || "（白板牌，没有能力）"}
            </div>
            {d.curated && <><div className="kb-ref-t">当前人工覆盖</div><pre className="kb-pre">{JSON.stringify(d.curated, null, 2)}</pre></>}
          </div>
        </div>
      )}
      <Foot busy={s.busy || del.busy} err={s.err || del.err} onCancel={onClose}
        extra={d?.curated ? <button className="btn danger-ghost" onClick={() => confirm("删除这张牌的人工覆盖？之后完全使用资料库。") && del.run(() => kb.deleteCard(id))}><i className="ti ti-trash" aria-hidden="true" />删除覆盖</button> : undefined}
        onSave={() => s.run(() => kb.putCard(id, { cost: num(cost), power: num(power), ability, notes, tags: d?.curated?.tags }))} />
    </Modal>
  );
}

import { useEffect, useMemo, useState } from "react";
import { Field, SettingsData, sys } from "../sysApi";

const same = (a: any, b: any) => (a ?? null) === (b ?? null) || String(a ?? "") === String(b ?? "");

function Input({ f, v, onChange }: { f: Field; v: any; onChange: (v: any) => void }) {
  if (f.type === "bool")
    return (
      <div className="seg sm">
        <button className={v === true ? "on" : ""} onClick={() => onChange(true)}>开</button>
        <button className={v === false ? "on danger" : ""} onClick={() => onChange(false)}>关</button>
      </div>
    );
  if (f.options && (f.type === "select" || f.type === "client" || f.type === "profile")) {
    if (f.options.length <= 4 && f.type === "select")
      return (
        <div className="seg sm">
          {f.options.map((o) => <button key={o} className={v === o ? "on" : ""} onClick={() => onChange(o)}>{o}</button>)}
        </div>
      );
    return (
      <select className="txt" value={v ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        {f.optional && <option value="">（默认）</option>}
        {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  if (f.type === "int" || f.type === "float")
    return (
      <div className="num-row">
        <input className="txt" type="number" step={f.type === "int" ? 1 : f.path.endsWith("_prob") ? 0.05 : 1} min={f.min} max={f.max}
          value={v ?? ""} placeholder={f.optional ? "留空 = 默认" : ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
        {f.min != null && <span className="hint">{f.min}–{f.max}</span>}
      </div>
    );
  return <input className="txt" value={v ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

export function Settings({ running }: { running: boolean }) {
  const [d, setD] = useState<SettingsData | null>(null);
  const [vals, setVals] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const load = () =>
    sys.settings().then((x) => {
      setD(x);
      setVals(Object.fromEntries(x.fields.map((f) => [f.path, f.value])));
    }).catch((e) => setErr(`读取设置失败：${e.message}`));
  useEffect(() => { load(); }, []);

  const dirty = useMemo(() => (d ? d.fields.filter((f) => !same(f.value, vals[f.path])).map((f) => f.path) : []), [d, vals]);
  const groups = useMemo(() => {
    const m = new Map<string, Field[]>();
    d?.fields.forEach((f) => m.set(f.group, [...(m.get(f.group) ?? []), f]));
    return [...m.entries()];
  }, [d]);

  const save = async () => {
    setErr("");
    setMsg("");
    try {
      const r = await sys.saveSettings(Object.fromEntries(dirty.map((p) => [p, vals[p]])));
      setMsg(r.changed.length ? `已保存 ${r.changed.length} 项${running ? "，下次启动机器人生效" : ""}` : "没有变化");
      load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  if (err && !d) return <div className="err">{err}</div>;
  if (!d) return <div className="brain-empty">读取中…</div>;
  return (
    <div className="settings-page">
      <div className="set-bar">
        <span className="hint"><i className="ti ti-file-settings" aria-hidden="true" /> config/settings.toml · 只开放常用项，保留文件里的注释；每次保存前备份到 logs/settings_backup/</span>
        <span className="sp" />
        {msg && <span className="ok-msg"><i className="ti ti-check" aria-hidden="true" />{msg}</span>}
        <button className="btn" disabled={!dirty.length} onClick={() => setVals(Object.fromEntries(d.fields.map((f) => [f.path, f.value])))}>还原</button>
        <button className="btn gold" disabled={!dirty.length} onClick={save}><i className="ti ti-device-floppy" aria-hidden="true" />保存{dirty.length ? `（${dirty.length}）` : ""}</button>
      </div>
      {err && <div className="err">{err}</div>}
      {running && <div className="kb-warn" style={{ margin: "0 0 12px" }}><i className="ti ti-alert-circle" aria-hidden="true" />机器人运行中：设置保存后，下次启动才生效</div>}
      <div className="set-grid">
        {groups.map(([g, fields]) => (
          <div key={g} className="panel set-group">
            <div className="ph"><span className="ph-tag">{g}</span></div>
            <div className="set-fields">
              {fields.map((f) => (
                <div key={f.path} className={`set-field ${dirty.includes(f.path) ? "dirty" : ""}`}>
                  <div className="set-label">
                    <b>{f.label}</b>
                    <code>{f.path}</code>
                  </div>
                  <Input f={f} v={vals[f.path]} onChange={(v) => setVals({ ...vals, [f.path]: v })} />
                  {f.help && <div className="hint">{f.help}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        <div className="panel set-group">
          <div className="ph"><span className="ph-tag">密钥</span><span className="ph-sub">只显示是否已配置；内容在项目根目录 .env 里修改（不入库）</span></div>
          <div className="set-fields">
            {d.secrets.map((s) => (
              <div key={s.name} className="secret-row">
                <code>{s.name}</code>
                <span className="hint">{s.used_by}</span>
                <span className={`badge ${s.set ? "ok" : "idle"}`}>{s.set ? "已配置" : "未配置"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

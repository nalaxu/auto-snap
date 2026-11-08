import { useEffect, useRef, useState } from "react";
import { RunView, sys, Tool } from "../sysApi";

export function Tools({ running }: { running: boolean }) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [sel, setSel] = useState<string>("");
  const [params, setParams] = useState<Record<string, number>>({});
  const [run, setRun] = useState<RunView | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [imgKey, setImgKey] = useState(0);
  const outRef = useRef<HTMLDivElement>(null);

  const loadTools = () => sys.tools().then((t) => { setTools(t); setSel((s) => s || t[0]?.id || ""); }).catch((e) => setErr(e.message));
  useEffect(() => { loadTools(); }, []);
  const tool = tools.find((t) => t.id === sel);

  useEffect(() => {
    if (!tool) return;
    setParams(Object.fromEntries(Object.entries(tool.params ?? {}).map(([k, p]) => [k, p.default])));
  }, [sel, tools.length]);

  // 轮询运行输出
  useEffect(() => {
    if (!run?.running) return;
    const t = window.setInterval(async () => {
      try {
        const v = await sys.runView(run.id, lines.length);
        if (v.lines.length) setLines((ls) => [...ls, ...v.lines]);
        if (!v.running) {
          setRun(v);
          loadTools();
          setImgKey((k) => k + 1);
        }
      } catch {
        /* 下次再试 */
      }
    }, 400);
    return () => window.clearInterval(t);
  }, [run?.id, run?.running, lines.length]);

  useEffect(() => {
    outRef.current?.scrollTo({ top: outRef.current.scrollHeight });
  }, [lines.length]);

  const start = async () => {
    if (!tool) return;
    if (tool.cost && !confirm(`「${tool.label}」会调用大模型，产生费用。继续？`)) return;
    setErr("");
    setLines([]);
    try {
      const v = await sys.run(tool.id, params);
      setRun(v);
      setLines(v.lines);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const busy = !!run?.running;
  return (
    <div className="tools-page">
      <div className="tool-list">
        {tools.map((t) => (
          <button key={t.id} className={`panel tool-card ${sel === t.id ? "on" : ""}`} onClick={() => !busy && setSel(t.id)} disabled={busy && sel !== t.id}>
            <i className={`ti ${t.icon}`} aria-hidden="true" />
            <div>
              <b>{t.label}</b>
              <span>{t.desc}</span>
              <div className="tool-tags">
                {t.game && <em>需要游戏</em>}
                {t.cost && <em className="cost">产生费用</em>}
              </div>
            </div>
          </button>
        ))}
      </div>

      <div className="panel tool-run">
        {tool ? (
          <>
            <div className="ph">
              <span className="ph-tag">{tool.label}</span>
              <span className="ph-sub">{run && run.tool === tool.id ? (run.running ? "运行中…" : `已结束 · 退出码 ${run.code}`) : "未运行"}</span>
            </div>
            <div className="tool-ctrl">
              {Object.entries(tool.params ?? {}).map(([k, p]) => (
                <label key={k} className="field inline">
                  <span>{p.label}</span>
                  <input className="txt" type="number" min={p.min} max={p.max} value={params[k] ?? p.default}
                    onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })} />
                </label>
              ))}
              <span className="sp" />
              {running && tool.id === "update_card_db" && <span className="hint danger">机器人运行中：资料库更新后它下次启动才会读到</span>}
              {busy && run?.tool === tool.id ? (
                <button className="btn stop" onClick={() => run && sys.stopRun(run.id)}><i className="ti ti-player-stop" aria-hidden="true" />中止</button>
              ) : (
                <button className="btn gold" disabled={busy} onClick={start}><i className="ti ti-player-play" aria-hidden="true" />运行</button>
              )}
            </div>
            {err && <div className="err" style={{ margin: "0 16px" }}>{err}</div>}
            <div className="tool-out" ref={outRef}>
              {run?.tool === tool.id && lines.length ? lines.map((l, i) => <div key={i}>{l || " "}</div>) : <div className="hint">点「运行」后，输出会显示在这里</div>}
            </div>
            {tool.image && tool.image_at && (
              <div className="tool-img">
                <div className="hint">最新截图（{new Date(tool.image_at * 1000).toLocaleString("zh-CN", { hour12: false })}）</div>
                <img src={`/api/tools/${tool.id}/image?k=${imgKey}-${tool.image_at}`} alt="坐标标定截图" />
              </div>
            )}
          </>
        ) : (
          <div className="brain-empty">读取中…</div>
        )}
      </div>
    </div>
  );
}

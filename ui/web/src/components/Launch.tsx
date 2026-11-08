import { useEffect, useState } from "react";
import { api, Options } from "../api";

/** 机器人没在运行时占据战场区域：选择客户端 / 演练或实操 / 决策器 / 局数，或回放一局历史对局。 */
export function Launch({ lastStop }: { lastStop?: string }) {
  const [opts, setOpts] = useState<Options | null>(null);
  const [client, setClient] = useState("");
  const [live, setLive] = useState(false);
  const [decider, setDecider] = useState("llm");
  const [maxGames, setMaxGames] = useState(1);
  const [skipCurrent, setSkipCurrent] = useState(false);
  const [demo, setDemo] = useState("");
  const [speed, setSpeed] = useState(2);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const load = () =>
    api.options().then((o) => {
      setOpts(o);
      setClient((c) => c || o.client);
      setDecider(o.decider || "llm");
      setDemo((d) => d || o.games[0]?.file || "");
    }).catch((e) => setErr(`读取配置失败：${e.message}`));

  useEffect(() => { load(); }, []);

  const start = async (body: Parameters<typeof api.start>[0]) => {
    setErr("");
    setBusy(true);
    try {
      await api.start(body);
    } catch (e: any) {
      setErr(`启动失败：${e.message}`);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  const go = () => (live ? setConfirm(true) : start({ client, live, decider, max_games: maxGames, skip_current: skipCurrent }));
  const cur = opts?.clients.find((c) => c.key === client);

  return (
    <div className="panel launch">
      <div className="ph" style={{ padding: "12px 0 0" }}>
        <span className="ph-tag">出征准备</span>
        <span className="ph-sub">{lastStop ? `上次运行：${lastStop}` : "机器人未运行"}</span>
        <span className="sp" />
        <button className="btn" onClick={load} title="刷新客户端运行状态"><i className="ti ti-refresh" aria-hidden="true" />刷新</button>
      </div>

      <div className="launch-grid">
        <div className="field">
          <label>客户端</label>
          <select value={client} onChange={(e) => setClient(e.target.value)}>
            {opts?.clients.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}{c.running ? " · 运行中" : ""}{c.verified ? "" : " · 未实测"}
              </option>
            ))}
          </select>
          <span className={`hint ${cur && !cur.running ? "danger" : ""}`}>
            {cur ? (cur.running ? "已检测到游戏进程" : "没检测到游戏进程：先启动游戏并停在主界面") : "读取中…"}
          </span>
        </div>

        <div className="field">
          <label>模式</label>
          <div className="seg">
            <button className={!live ? "on" : ""} onClick={() => setLive(false)}>演练</button>
            <button className={live ? "on danger" : ""} onClick={() => setLive(true)}>实操</button>
          </div>
          <span className={`hint ${live ? "danger" : ""}`}>
            {live ? "实操会接管鼠标，游戏窗口会被切到前台；按住 F8 或点停止随时中止" : "只感知和决策，不动鼠标"}
          </span>
        </div>

        <div className="field">
          <label>决策</label>
          <div className="seg">
            <button className={decider === "llm" ? "on" : ""} onClick={() => setDecider("llm")}>大模型{opts?.model ? ` · ${opts.model}` : ""}</button>
            <button className={decider === "rule_based" ? "on" : ""} onClick={() => setDecider("rule_based")}>规则 AI</button>
          </div>
          <span className="hint">大模型超时或不合法时自动由规则 AI 兜底</span>
        </div>

        <div className="field">
          <label>打几局（0 = 不限）</label>
          <input type="number" min={0} max={200} value={maxGames} onChange={(e) => setMaxGames(Math.max(0, Number(e.target.value) || 0))} />
          <label className="check">
            <input type="checkbox" checked={skipCurrent} onChange={(e) => setSkipCurrent(e.target.checked)} />
            跳过当前回合（对局进行中接管时用）
          </label>
        </div>
      </div>

      {err && <div className="err">{err}</div>}

      <div className="launch-foot">
        <div className="field" style={{ flex: 1, maxWidth: 460 }}>
          <label>回放演示（不连游戏，用历史对局重放）</label>
          <div className="demo-row">
            <select value={demo} onChange={(e) => setDemo(e.target.value)}>
              {opts?.games.map((g) => (
                <option key={g.file} value={g.file}>
                  {g.started?.slice(5, 16).replace("T", " ")} · {g.result === "win" ? `胜 +${g.cube_value}` : g.result === "loss" ? `负 −${g.cube_value}` : `${g.result} ${g.cube_value}`} · {g.turns_recorded} 回合
                </option>
              ))}
            </select>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} style={{ maxWidth: 90 }}>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={4}>4×</option>
            </select>
            <button className="btn" disabled={!demo || busy} onClick={() => start({ demo, speed })}>
              <i className="ti ti-player-play" aria-hidden="true" />回放
            </button>
          </div>
        </div>
        <span className="sp" />
        <button className="btn gold big" disabled={busy || !client} onClick={go}>
          <i className="ti ti-swords" aria-hidden="true" />{live ? "开始实操" : "开始演练"}
        </button>
      </div>

      {confirm && (
        <div className="modal-back" onClick={() => setConfirm(false)}>
          <div className="panel modal" onClick={(e) => e.stopPropagation()}>
            <h3>开始实操？</h3>
            <p>
              机器人会接管鼠标，在「{cur?.label ?? client}」里自动对局{maxGames ? ` ${maxGames} 局` : "（不限局数）"}。
              运行期间不要操作鼠标；按住 <kbd>F8</kbd> 或点面板上的停止可以随时中止。
            </p>
            <div className="row">
              <button className="btn" onClick={() => setConfirm(false)}>取消</button>
              <button className="btn stop" disabled={busy} onClick={() => start({ client, live: true, decider, max_games: maxGames, skip_current: skipCurrent })}>
                <i className="ti ti-swords" aria-hidden="true" />开始实操
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

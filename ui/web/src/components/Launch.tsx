import { useEffect, useState } from "react";
import { api, Options } from "../api";

// 上次启动时的选项（每个浏览器各自记住）：用脚本重启面板后不用重新选，尤其是「实操」——否则默认回到演练，看起来像卡住
const SAVED_KEY = "autosnap.launch";
type Saved = Partial<{ client: string; live: boolean; decider: string; max_games: number; skip_current: boolean;
  game_mode: "ranked" | "arena"; max_runs: number; buy_tickets: boolean }>;
function loadSaved(): Saved {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY) || "{}") || {}; } catch { return {}; }
}
function saveLaunch(v: Saved) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(v)); } catch { /* 隐私模式等：不记 */ }
}

/** 机器人没在运行时占据战场区域：选择客户端 / 演练或实操 / 决策器 / 局数，或回放一局历史对局。 */
export function Launch({ lastStop }: { lastStop?: string }) {
  const [saved] = useState(loadSaved);
  const [opts, setOpts] = useState<Options | null>(null);
  const [client, setClient] = useState(saved.client ?? "");
  const [live, setLive] = useState(saved.live ?? false);
  const [decider, setDecider] = useState(saved.decider ?? "llm");
  const [maxGames, setMaxGames] = useState(saved.max_games ?? 1);
  const [gameMode, setGameMode] = useState<"ranked" | "arena">(saved.game_mode ?? "ranked");
  const [maxRuns, setMaxRuns] = useState(saved.max_runs ?? 0);
  const [buyTickets, setBuyTickets] = useState(saved.buy_tickets ?? false);
  const [skipCurrent, setSkipCurrent] = useState(false);
  const [demo, setDemo] = useState("");
  const [speed, setSpeed] = useState(2);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const load = () =>
    api.options().then((o) => {
      setOpts(o);
      // 记住的选项优先，没有才用配置文件里的默认值；记住的客户端已不存在时退回默认
      setClient((c) => (c && o.clients.some((x) => x.key === c) ? c : o.client));
      if (saved.decider === undefined) setDecider(o.decider || "llm");
      if (saved.game_mode === undefined) setGameMode((m) => (o.game_mode === "arena" ? "arena" : m));
      if (o.arena && saved.buy_tickets === undefined) setBuyTickets(o.arena.buy_tickets);
      if (o.arena && saved.max_runs === undefined) setMaxRuns(o.arena.max_runs);
      setDemo((d) => d || o.games[0]?.file || "");
    }).catch((e) => setErr(`读取配置失败：${e.message}`));

  useEffect(() => { load(); }, []);

  const start = async (body: Parameters<typeof api.start>[0]) => {
    setErr("");
    setBusy(true);
    try {
      await api.start(body);
      if (!("demo" in body)) saveLaunch({ ...(body as Saved), max_runs: maxRuns, buy_tickets: buyTickets });
    } catch (e: any) {
      setErr(`启动失败：${e.message}`);
    } finally {
      setBusy(false);
      setConfirm(false);
    }
  };

  const arena = gameMode === "arena";
  const body = (isLive: boolean) => ({
    client, live: isLive, decider, max_games: maxGames, skip_current: skipCurrent, game_mode: gameMode,
    ...(arena ? { max_runs: maxRuns, buy_tickets: buyTickets } : {}),
  });
  const go = () => (live ? setConfirm(true) : start(body(false)));
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
          <label>玩法</label>
          <div className="seg">
            <button className={!arena ? "on" : ""} onClick={() => setGameMode("ranked")}>排位</button>
            <button className={arena ? "on" : ""} onClick={() => setGameMode("arena")}>竞技场</button>
          </div>
          <span className="hint">
            {arena ? "从主界面进入 Game Modes → Arena Mode；组卡 / 换牌由大模型决定，Snap / 撤退按血量与宝箱档位算整场期望" : "主界面点 PLAY，用当前选中的卡组"}
          </span>
        </div>

        {arena ? (
          <div className="field">
            <label>打几场竞技（0 = 不限）</label>
            <input type="number" min={0} max={50} value={maxRuns} onChange={(e) => setMaxRuns(Math.max(0, Number(e.target.value) || 0))} />
            <label className="check">
              <input type="checkbox" checked={buyTickets} onChange={(e) => setBuyTickets(e.target.checked)} />
              门票不足时用金块购买（150 金块 / 张；有门票时总是先用门票）
            </label>
          </div>
        ) : <div />}

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
          <label>{arena ? "最多打几局对局（0 = 不限，按场数停）" : "打几局（0 = 不限）"}</label>
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
              机器人会接管鼠标，在「{cur?.label ?? client}」里自动{arena ? `打竞技场${maxRuns ? ` ${maxRuns} 场` : "（不限场数）"}${maxGames ? `，最多 ${maxGames} 局对局` : ""}${buyTickets ? "；门票不足时会花 150 金块买票" : "；没有门票就停下"}` : `对局${maxGames ? ` ${maxGames} 局` : "（不限局数）"}`}。
              运行期间不要操作鼠标；按住 <kbd>F8</kbd> 或点面板上的停止可以随时中止。
            </p>
            <div className="row">
              <button className="btn" onClick={() => setConfirm(false)}>取消</button>
              <button className="btn stop" disabled={busy} onClick={() => start(body(true))}>
                <i className="ti ti-swords" aria-hidden="true" />开始实操
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

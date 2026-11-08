import { useState } from "react";
import { api } from "../api";
import { Live, sessionRecord } from "../store";

const PHASE: Record<string, string> = {
  starting: "启动中",
  menu: "主界面",
  matching: "匹配中",
  in_game: "对局中",
  guide: "新手指引",
  paused: "已暂停",
  idle: "未运行",
};

function Brand() {
  return (
    <div className="brand">
      <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
        <polygon points="20,2 36,11 36,29 20,38 4,29 4,11" fill="#ffc63d" />
        <polygon points="20,2 36,11 20,20 4,11" fill="#fff2b0" />
        <polygon points="20,20 36,11 36,29 20,38" fill="#ff9a1a" />
        <path d="M14 26 L20 14 L26 26 M16.5 22 H23.5" stroke="#3a2200" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div>
        <div className="brand-word">AUTO·SNAP</div>
        <div className="brand-sub">自动对局指挥台</div>
      </div>
    </div>
  );
}

export function TopBar({ s }: { s: Live }) {
  const [busy, setBusy] = useState(false);
  const rec = sessionRecord(s.results);
  const st = s.turn;
  const stake = st?.cube_stake ?? 1;
  const next = st?.cube_next && st.cube_next !== stake ? st.cube_next : null;
  const gameNo = s.results.length + (s.gameId && !s.results.some((r) => r.game_id === s.gameId) ? 1 : 0);
  const send = async (cmd: "pause" | "resume" | "stop" | "kill") => {
    setBusy(true);
    try {
      await api.control(cmd);
    } catch {
      /* 进程已退出等：状态会通过事件更新 */
    } finally {
      setBusy(false);
    }
  };

  const ses = s.session;
  const modeChip = ses?.demo ? <span className="chip mode demo">回放</span> : ses?.live ? <span className="chip mode">实操</span> : ses ? <span className="chip mode dry">演练</span> : null;

  return (
    <header className="top">
      <Brand />
      <div className="chips">
        {s.running ? (
          <span className={`chip ${s.paused || s.stopping ? "warn" : "run"}`}>
            <span className="dot" />
            {s.stopping ? "正在停止" : s.paused ? "本局后暂停" : PHASE[s.phase] ?? s.phase}
            {gameNo > 0 && <> · 第 <b>{gameNo}</b> 局</>}
          </span>
        ) : (
          <span className="chip idle"><span className="dot" />未运行</span>
        )}
        {s.running && modeChip}
        {ses && s.running && (
          <span className="chip">
            <i className="ti ti-device-desktop" aria-hidden="true" /><b>{ses.client_label}</b>·
            <i className="ti ti-brain" aria-hidden="true" /><b>{ses.model}</b>
            {s.deckName && <>·<i className="ti ti-cards" aria-hidden="true" /><b>{s.deckName}</b></>}
          </span>
        )}
        {!s.connected && <span className="chip warn"><span className="conn" />面板后端未连接</span>}
      </div>

      <div className="turnbox">
        <div>
          <div className="turn-label">回合</div>
          <div className="turn-num">{st ? st.turn : "–"}<small>/{st?.max_turn ?? 6}</small></div>
        </div>
        <div className="pips">
          {Array.from({ length: st?.max_turn ?? 6 }).map((_, i) => (
            <i key={i} className={`pip ${st && i + 1 < st.turn ? "done" : ""} ${st && i + 1 === st.turn ? "now" : ""}`} />
          ))}
        </div>
      </div>
      <div className="stake">
        <div className="cube" aria-hidden="true">
          <div className="cube-in">
            {[0, 1, 2, 3].map((i) => <div key={i} className="cube-f">{next ?? stake}</div>)}
          </div>
        </div>
        <div className="stake-txt">
          <div className="k">当前赌注{st?.opp_snapped ? " · 对手已 Snap" : ""}</div>
          <div className="v">{next ? <>{stake} → <em>{next}</em></> : <em>{stake}</em>}</div>
        </div>
      </div>
      <div className="record">
        <div className="rec-item"><div className="v win">{rec.w}-{rec.l}</div><div className="k">本次胜负</div></div>
        <div className="rec-item"><div className="v cubes">{rec.cubes > 0 ? "+" : ""}{rec.cubes}</div><div className="k">魔方</div></div>
      </div>
      <div className="ctrl">
        {s.running && !ses?.demo && (
          s.paused ? (
            <button className="btn" disabled={busy} onClick={() => send("resume")}><i className="ti ti-player-play" aria-hidden="true" />继续开局</button>
          ) : (
            <button className="btn" disabled={busy} onClick={() => send("pause")}><i className="ti ti-player-pause" aria-hidden="true" />本局后暂停</button>
          )
        )}
        {s.running && s.stopping ? (
          <button className="btn stop" disabled={busy} onClick={() => send("kill")} title="立即结束进程"><i className="ti ti-skull" aria-hidden="true" />强制结束</button>
        ) : (
          <button className="btn stop" disabled={busy || !s.running} onClick={() => send("stop")}><i className="ti ti-player-stop" aria-hidden="true" />停止 <kbd>F8</kbd></button>
        )}
      </div>
    </header>
  );
}

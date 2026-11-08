import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { Live, sessionRecord } from "../store";

const PHASE: Record<string, string> = {
  starting: "启动中", menu: "主界面", matching: "匹配中", in_game: "对局中", guide: "新手指引", paused: "已暂停", idle: "未运行",
};

/** 悬浮小条：一眼看到阶段、回合、赌注、大模型进度 / 本回合行动、胜率，并能暂停 / 停止。 */
export function MiniView({ s, now }: { s: Live; now: number }) {
  const st = s.turn;
  const d = s.decision;
  const rec = sessionRecord(s.results);
  const thinking = s.running && st && !d && s.turnAt != null;
  const elapsed = thinking ? now - (s.turnAt ?? now) : d?.decide_s ?? 0;
  const deadline = s.deadline ?? 0;
  let left: number | null = null;
  if (st?.timer_left != null && s.turnAt) left = Math.max(0, st.timer_left - (now - s.turnAt));
  const send = (cmd: "pause" | "resume" | "stop" | "kill") => api.control(cmd).catch(() => {});
  const acts = d
    ? [d.snap && "SNAP", d.retreat && "撤退", ...d.activate.map((a) => `激活 ${a.name}`), ...d.moves.map((m) => `移 ${m.name}`),
       ...d.actions.map((a) => `${a.name}→${a.location_name}`)].filter(Boolean).join(" · ") || "不出牌"
    : "";
  const live = s.running && s.phase === "in_game" && st;
  return (
    <div className="mini">
      <div className="mini-top">
        <span className={`mini-dot ${s.running ? (s.paused || s.stopping ? "warn" : "run") : "idle"}`} />
        <b>{s.running ? (s.stopping ? "正在停止" : s.paused ? "本局后暂停" : PHASE[s.phase] ?? s.phase) : "未运行"}</b>
        {s.session && s.running && <span className={`mini-mode ${s.session.demo ? "demo" : s.session.live ? "live" : "dry"}`}>{s.session.demo ? "回放" : s.session.live ? "实操" : "演练"}</span>}
        <span className="sp" />
        <span className="mini-rec"><em>{rec.w}-{rec.l}</em><em className={rec.cubes >= 0 ? "pos" : "neg"}>{rec.cubes > 0 ? "+" : ""}{rec.cubes}</em></span>
      </div>
      {live ? (
        <div className="mini-mid">
          <div className="mini-turn"><b>T{st.turn}</b><small>/{st.max_turn}</small></div>
          <div className="mini-cube">{st.cube_next && st.cube_next !== st.cube_stake ? `${st.cube_stake}→${st.cube_next}` : st.cube_stake}</div>
          <div className="mini-info">
            {thinking ? (
              <>
                <div className="mini-line"><i className="ti ti-brain" aria-hidden="true" />思考中 {elapsed.toFixed(1)}s{deadline ? ` / ${deadline.toFixed(0)}s` : ""}</div>
                <div className="mini-bar"><span style={{ width: `${deadline ? Math.min(100, (elapsed / deadline) * 100) : 0}%` }} className={deadline && elapsed / deadline > 0.8 ? "hot" : ""} /></div>
              </>
            ) : d ? (
              <>
                <div className="mini-line" title={acts}><i className={`ti ${d.fallback ? "ti-robot" : "ti-cards"}`} aria-hidden="true" />{acts}</div>
                <div className="mini-sub">
                  {d.fallback ? "规则 AI" : `${(d.decide_s ?? 0).toFixed(1)}s`}
                  {d.win_prob != null && <> · 胜率 <b>{Math.round(d.win_prob * 100)}%</b></>}
                  {left != null && <> · 剩 {Math.ceil(left)}s</>}
                </div>
              </>
            ) : <div className="mini-line">等待我方回合</div>}
          </div>
        </div>
      ) : (
        <div className="mini-mid idle">{s.running ? "等待对局 / 我方回合…" : s.stopReason ? `上次：${s.stopReason}` : "在面板里启动机器人"}</div>
      )}
      <div className="mini-ctrl">
        {s.running && !s.session?.demo && (s.paused
          ? <button onClick={() => send("resume")}><i className="ti ti-player-play" aria-hidden="true" />继续</button>
          : <button onClick={() => send("pause")}><i className="ti ti-player-pause" aria-hidden="true" />本局后暂停</button>)}
        {s.running && <button className="stop" onClick={() => send(s.stopping ? "kill" : "stop")}><i className="ti ti-player-stop" aria-hidden="true" />{s.stopping ? "强制结束" : "停止"}</button>}
      </div>
    </div>
  );
}

declare global {
  interface Window {
    documentPictureInPicture?: { requestWindow: (o: { width: number; height: number }) => Promise<Window>; window: Window | null };
  }
}

/** 打开置顶悬浮窗：支持 Document Picture-in-Picture（Chrome / Edge 116+）时置顶，否则开普通小窗口（#/mini）。 */
export function usePip() {
  const [win, setWin] = useState<Window | null>(null);
  const open = useCallback(async () => {
    if (win) {
      win.focus();
      return;
    }
    const pip = window.documentPictureInPicture;
    if (!pip) {
      window.open(`${location.pathname}#/mini`, "autosnap-mini", "width=440,height=190");
      return;
    }
    const w = await pip.requestWindow({ width: 440, height: 190 });
    for (const node of Array.from(document.head.querySelectorAll("link[rel=stylesheet], style"))) {
      w.document.head.appendChild(node.cloneNode(true));
    }
    w.document.title = "Auto-Snap";
    w.document.body.className = "mini-body";
    w.addEventListener("pagehide", () => setWin(null));
    setWin(w);
  }, [win]);
  useEffect(() => () => win?.close(), [win]);
  return { win, open };
}

export function PipPortal({ win, children }: { win: Window | null; children: React.ReactNode }) {
  return win ? createPortal(children, win.document.body) : null;
}

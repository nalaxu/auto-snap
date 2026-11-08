import { useState } from "react";
import type { Live } from "../store";

const KIND_TEXT: Record<string, string> = {
  play: "出牌", llm: "决策", state: "状态", snap: "SNAP", warn: "注意", ok: "完成", info: "系统", end: "结算",
};

const hms = (t: number) => new Date(t * 1000).toLocaleTimeString("zh-CN", { hour12: false });

export function Feed({ s }: { s: Live }) {
  const [raw, setRaw] = useState(false);
  const [tall, setTall] = useState(false);
  return (
    <div className="panel feed">
      <div className="ph">
        <span className="ph-tag">战报</span>
        <span className="ph-sub">{raw ? "机器人控制台输出" : "结构化事件"}</span>
        <div className="feed-tabs">
          <button className={!raw ? "on" : ""} onClick={() => setRaw(false)}>战报</button>
          <button className={raw ? "on" : ""} onClick={() => setRaw(true)}>原始日志</button>
          <button onClick={() => setTall(!tall)} aria-label={tall ? "收起" : "展开"}>
            <i className={`ti ${tall ? "ti-chevron-up" : "ti-chevron-down"}`} aria-hidden="true" />
          </button>
        </div>
      </div>
      {raw ? (
        <div className={`feed-list raw ${tall ? "tall" : ""}`}>
          {s.logs.length ? s.logs.map((l, i) => <div key={s.logs.length - i}>{l.text}</div>) : <div>还没有输出</div>}
        </div>
      ) : (
        <div className={`feed-list ${tall ? "tall" : ""}`}>
          {s.feed.length ? (
            s.feed.map((f, i) => (
              <div key={`${f.t}-${s.feed.length - i}`} className={`feed-row ${i === 0 ? "new" : ""}`}>
                <span className="t">{hms(f.t)}</span>
                <span className="turn">{f.turn ? `T${f.turn}` : ""}</span>
                <span className={`k ${f.kind}`}>{KIND_TEXT[f.kind] ?? f.kind}</span>
                <span>{f.text}</span>
              </div>
            ))
          ) : (
            <div className="feed-row"><span className="t" /><span /><span /><span>启动机器人后，这里按时间显示每一步</span></div>
          )}
        </div>
      )}
    </div>
  );
}

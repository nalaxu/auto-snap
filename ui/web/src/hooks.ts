import { useEffect, useReducer, useState } from "react";
import { Ev, initial, Live, reduce } from "./store";

/** 连接 /ws，断线自动重连；返回由事件重建的页面状态。 */
export function useLive(): Live {
  const [state, dispatch] = useReducer(reduce, initial);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = (m) => dispatch(JSON.parse(m.data) as Ev);
      ws.onclose = () => {
        dispatch({ kind: "__disconnected" });
        if (!closed) timer = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);
  return state;
}

/** 每 interval 毫秒刷新一次的当前时间（秒）。 */
export function useNow(interval = 250): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), interval);
    return () => window.clearInterval(id);
  }, [interval]);
  return now;
}

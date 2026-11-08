import { Board } from "./components/Board";
import { Brain } from "./components/Brain";
import { Feed } from "./components/Feed";
import { Launch } from "./components/Launch";
import { TopBar } from "./components/TopBar";
import { useLive, useNow } from "./hooks";
import { Review } from "./pages/Review";
import { Stats } from "./pages/Stats";
import { Route, useRoute } from "./route";
import type { Live } from "./store";

const NAV: { icon: string; label: string; page?: Route["page"]; href?: string; soon?: string }[] = [
  { icon: "ti-swords", label: "实时", page: "live", href: "#/live" },
  { icon: "ti-chart-arcs", label: "战绩", page: "stats", href: "#/stats" },
  { icon: "ti-history", label: "复盘", page: "review", href: "#/review" },
  { icon: "ti-books", label: "知识库", soon: "M3" },
  { icon: "ti-tool", label: "工具", soon: "M4" },
  { icon: "ti-adjustments-horizontal", label: "设置", soon: "M4" },
];

const WAIT_TEXT: Record<string, [string, string]> = {
  starting: ["正在启动", "附加游戏进程、定位内存结构（有缓存时几乎瞬间完成）"],
  menu: ["在主界面", "识别到 PLAY 按钮后会自动开下一局"],
  matching: ["匹配中", "已点击 PLAY，等待对手"],
  in_game: ["对手回合 / 揭示中", "轮到我方出牌时显示局面"],
  guide: ["新手指引", "程序已暂停，请跟随游戏指引点击"],
  paused: ["已暂停", "停在主界面，点「继续开局」恢复"],
};

function Waiting({ s }: { s: Live }) {
  const [big, sm] = WAIT_TEXT[s.phase] ?? ["等待中", ""];
  return (
    <div className="panel board">
      <div className="empty">
        {s.phase === "paused" ? <i className="ti ti-player-pause" aria-hidden="true" /> : <div className="spinner" />}
        <div className="big">{big}</div>
        <div className="sm">{sm}</div>
      </div>
    </div>
  );
}

function LivePage({ s }: { s: Live }) {
  const now = useNow();
  const showBoard = s.running && s.turn && s.phase !== "menu" && s.phase !== "matching" && s.phase !== "paused";
  return (
    <div className="live">
      <div>
        {!s.running ? <Launch lastStop={s.stopReason} /> : showBoard ? <Board s={s} now={now} /> : <Waiting s={s} />}
        <Feed s={s} />
      </div>
      <Brain s={s} now={now} />
    </div>
  );
}

export default function App() {
  const s = useLive();
  const route = useRoute();
  return (
    <>
      <div className="grain" />
      <div className="app">
        <TopBar s={s} />
        <nav className="nav">
          {NAV.map((n) =>
            n.href ? (
              <a key={n.label} href={n.href} className={route.page === n.page ? "on" : ""}>
                <i className={`ti ${n.icon}`} aria-hidden="true" />
                {n.label}
                {n.page === "live" && s.running && route.page !== "live" && <span className="live-dot" title="机器人运行中" />}
              </a>
            ) : (
              <a key={n.label} className="off" title={`${n.soon} 阶段开放`}>
                <i className={`ti ${n.icon}`} aria-hidden="true" />
                {n.label}
                <span className="soon">{n.soon}</span>
              </a>
            ),
          )}
          <div className="sep" />
          <div className="ver">M2<br />只读内存</div>
        </nav>
        <main>
          {route.page === "stats" ? <Stats /> : route.page === "review" ? <Review file={route.arg} /> : <LivePage s={s} />}
        </main>
      </div>
    </>
  );
}

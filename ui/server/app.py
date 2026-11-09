"""外部面板后端：FastAPI，只监听 127.0.0.1。

    python main.py ui              # 启动面板并打开浏览器（http://127.0.0.1:8765）

接口：
    GET  /api/options   客户端列表（是否运行 / 已实测）、默认决策器与模型、可回放的对局
    GET  /api/status    机器人进程状态
    /api/settings       设置（GET / PUT，保留注释）     /api/tools/...  维护脚本（白名单）
    /api/kb/...         知识库：待复核、信号、对手卡组候选、卡组、卡牌 / 场地人工覆盖（见 kb.py）
    /api/arena/ratings  竞技场评分表复核（见 arena.py）
    GET  /api/stats     战绩总览（?client=&last=&mode=）  GET /api/arena/stats 竞技场战绩
    GET  /api/games     对局列表（?mode=）                GET /api/games/{file} 单局逐回合
    POST /api/start     启动机器人 {client, live, decider, max_games, max_turns, skip_current, game_mode, max_runs, buy_tickets}
                        或回放 {demo: 文件名, speed}
    POST /api/control   {cmd: pause | resume | stop | kill}
    WS   /ws            先发 hello（含本次运行的全部事件，页面据此重建状态），之后实时推送事件
前端静态文件：ui/web/dist（cd ui/web && npm install && npm run build）
"""
from __future__ import annotations

import asyncio
import json
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .arena import create_router as create_arena_router
from .bot import ROOT, BotProcess
from .kb import KnowledgeBase
from .kb import create_router as create_kb_router
from .settings import Settings
from .settings import create_router as create_settings_router
from .tools import ToolRunner
from .tools import create_router as create_tools_router

DIST = ROOT / "ui" / "web" / "dist"
GAMES_DIR = ROOT / "logs" / "games"


class StartRequest(BaseModel):
    client: str | None = None
    live: bool = False
    decider: str | None = None
    max_games: int = 0
    max_turns: int = 0
    skip_current: bool = False
    game_mode: str | None = None        # ranked | arena（None = config [game].mode）
    max_runs: int | None = None         # 竞技场：打几场（0 = 不限）
    buy_tickets: bool | None = None     # 竞技场：门票不足时用金块买（None = config [arena]）
    verbose: bool = False
    demo: str | None = None
    speed: float = 1.0
    loop: bool = False


class ControlRequest(BaseModel):
    cmd: str


class Hub:
    """把读线程里的事件转给所有 WebSocket 连接。"""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queues: set[asyncio.Queue] = set()

    def publish_threadsafe(self, ev: dict) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._fanout, ev)

    def _fanout(self, ev: dict) -> None:
        for q in list(self.queues):
            if q.qsize() < 5000:
                q.put_nowait(ev)


def load_settings() -> dict:
    with open(ROOT / "config" / "settings.toml", "rb") as f:
        return tomllib.load(f)


def recent_games(limit: int = 30) -> list[dict]:
    index = GAMES_DIR / "index.jsonl"
    if not index.exists():
        return []
    rows = []
    for line in index.read_text("utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out = []
    for r in reversed(rows):
        if (GAMES_DIR / str(r.get("file", ""))).exists():
            out.append({k: r.get(k) for k in ("file", "started", "result", "cube_value", "turns_recorded", "locations")})
        if len(out) >= limit:
            break
    return out


def create_app(data_dir: Path | None = None, settings_path: Path | None = None) -> FastAPI:
    hub = Hub()
    bot = BotProcess(hub.publish_threadsafe)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.loop = asyncio.get_running_loop()
        yield
        if bot.running:   # 关闭面板时停止机器人
            bot.send("stop")

    app = FastAPI(title="Auto-Snap 面板", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.hub, app.state.bot = hub, bot
    kb = KnowledgeBase(data_dir or ROOT / "data", (data_dir.parent if data_dir else ROOT / "logs") / "kb_backup", GAMES_DIR)
    app.state.kb = kb
    app.include_router(create_kb_router(kb))
    settings = Settings(settings_path or ROOT / "config" / "settings.toml", ROOT,
                        (settings_path.parent if settings_path else ROOT / "logs") / "settings_backup")
    app.include_router(create_settings_router(settings))
    app.state.tools = ToolRunner(ROOT)
    app.include_router(create_tools_router(app.state.tools))
    app.include_router(create_arena_router())

    @app.get("/api/options")
    def options() -> dict:
        import clients

        settings = load_settings()
        current = clients.selected_key(settings)
        items = []
        for key, c in clients.profiles(settings).items():
            try:
                running = bool(c.find_pids())
            except Exception:  # noqa: BLE001
                running = False
            items.append({"key": key, "label": c.label, "running": running, "verified": c.verified})
        task = settings.get("llm", {}).get("tasks", {}).get("decision", {})
        arena = settings.get("arena", {}) or {}
        return {
            "game_mode": settings.get("game", {}).get("mode", "ranked"),
            "arena": {"buy_tickets": bool(arena.get("buy_ticket_with_gold", False)), "max_runs": int(arena.get("max_runs", 0)),
                      "draft_decider": arena.get("draft_decider", "llm")},
            "clients": items,
            "client": current,
            "decider": settings.get("brain", {}).get("decider", "llm"),
            "model": task.get("model"),
            "games": recent_games(),
        }

    @app.get("/api/status")
    def status() -> dict:
        return bot.status()

    def _filtered(client: str | None, last: int, mode: str | None = None) -> list[dict]:
        from gamelog.stats import list_games

        games = list_games(GAMES_DIR)
        if client:
            games = [g for g in games if (g["client"] or "?") == client]
        if mode:
            games = [g for g in games if g.get("mode", "ranked") == mode]
        return games[-last:] if last > 0 else games

    @app.get("/api/stats")
    def stats(client: str | None = None, last: int = 0, mode: str | None = None) -> dict:
        """战绩总览。client = 客户端 key（早期没记录客户端的对局用 ?），last = 只看最近 N 局，mode = ranked | arena。"""
        from gamelog.stats import aggregate, list_games

        all_games = list_games(GAMES_DIR)
        return {**aggregate(_filtered(client, last, mode), GAMES_DIR),
                "clients": sorted({g["client"] or "?" for g in all_games})}

    @app.get("/api/arena/stats")
    def arena_stats() -> dict:
        """竞技场战绩：整场列表、平均积分、档位分布、按血量分组的对局表现、没收观测。"""
        from gamelog.arena_stats import aggregate as arena_aggregate

        return arena_aggregate(arena_games=_filtered(None, 0, "arena"))

    @app.get("/api/games")
    def games(client: str | None = None, last: int = 0, mode: str | None = None) -> list[dict]:
        """对局列表（新的在前）。"""
        return list(reversed(_filtered(client, last, mode)))

    @app.get("/api/games/{file}")
    def game(file: str) -> dict:
        from gamelog.stats import game_detail

        d = game_detail(file, GAMES_DIR)
        if d is None:
            raise HTTPException(404, "找不到这局对局记录")
        return d

    @app.post("/api/start")
    def start(req: StartRequest) -> dict:
        opts = req.model_dump()
        if req.demo:
            path = (GAMES_DIR / req.demo).resolve()
            if path.parent != GAMES_DIR.resolve() or not path.exists():
                raise HTTPException(404, "找不到这局对局记录")
            opts["demo"] = str(path)
        try:
            return bot.start(opts)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/control")
    def control(req: ControlRequest) -> dict:
        if req.cmd == "kill":
            ok = bot.kill()
        elif req.cmd in ("pause", "resume", "stop"):
            ok = bot.send(req.cmd)
        else:
            raise HTTPException(400, "未知命令")
        if not ok:
            raise HTTPException(409, "机器人没有在运行")
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q: asyncio.Queue = asyncio.Queue()
        hub.queues.add(q)
        try:
            await sock.send_json({"kind": "hello", "status": bot.status(), "history": list(bot.history)})
            while True:
                ev = await q.get()
                await sock.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.queues.discard(q)

    if (DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def missing() -> str:
            return ("<meta charset=utf-8><body style='font-family:sans-serif;background:#0d0b2b;color:#eee;padding:40px'>"
                    "<h2>前端还没构建</h2><p>在项目根目录运行：</p>"
                    "<pre>cd ui/web\nnpm install\nnpm run build</pre><p>然后刷新本页。</p></body>")
    return app


def serve(port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{port}"
    print(f"Auto-Snap 面板：{url}（Ctrl+C 退出；退出时会停止正在运行的机器人）")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")

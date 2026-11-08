"""工具（外部面板 M4）：在面板里运行 tools/ 下的维护脚本，输出逐行显示。

只运行 TOOLS 白名单里的命令，参数只接受白名单声明过的字段（类型校验后拼命令行），不接受任意命令。
同一时刻只跑一个工具；页面轮询 GET /api/tools/runs/{id}?since=N 取新输出。
"""
from __future__ import annotations

import itertools
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

MAX_LINES = 5000

# id -> 说明；argv 用 {参数名} 占位；params 声明可填参数
TOOLS: dict[str, dict] = {
    "clients": {"label": "客户端状态", "icon": "ti-device-desktop", "argv": ["main.py", "clients"],
                "desc": "各客户端是否在运行、卡组目录是否存在", "game": False},
    "detect_client": {"label": "检测客户端安装", "icon": "ti-zoom-scan", "argv": ["tools/detect_client.py"],
                      "desc": "新装客户端后：查看进程路径、标志文件、匹配的配置档", "game": False},
    "llm_check": {"label": "大模型连通性", "icon": "ti-plug-connected", "argv": ["tools/llm_check.py"],
                  "desc": "测试模型服务能否连通、结构化输出是否正常（会产生少量调用费用）", "game": False, "cost": True},
    "analyze_games": {"label": "赛后复盘报告", "icon": "ti-report-analytics", "argv": ["tools/analyze_games.py"],
                      "params": {"last": {"type": "int", "label": "只看最近 N 局（0 = 全部）", "default": 0, "min": 0, "max": 500,
                                          "flag": "--last"}},
                      "desc": "战绩、场地胜负、未用完能量的回合、被拒出牌；报告存到 logs/reports/", "game": False},
    "update_card_db": {"label": "更新卡牌 / 场地资料库", "icon": "ti-database-import", "argv": ["tools/update_card_db.py"],
                       "desc": "从公开图鉴抓取国际服卡牌和场地，打印新增 / 变更（国服缺的卡用它兜底；运行中的机器人下次启动生效）", "game": False},
    "build_cn_card_db": {"label": "读取国服卡牌库", "icon": "ti-database-search", "argv": ["tools/build_cn_card_db.py"],
                         "desc": "从正在运行的国服客户端内存读取当前卡牌（费用 / 战力 / 能力），列出与国际服不同的卡（约 1 分钟，只读内存）",
                         "game": True},
    "calibrate": {"label": "截图核对坐标", "icon": "ti-crosshair", "argv": ["tools/calibrate.py"],
                  "desc": "截游戏窗口并画出手牌、地点、按钮的点击坐标（需要游戏在对局中）", "game": True, "image": "logs/calibration.png"},
    "bench_decision": {"label": "模型速度对比", "icon": "ti-stopwatch", "argv": ["tools/bench_decision.py"],
                       "params": {"reps": {"type": "int", "label": "每回合重复次数", "default": 1, "min": 1, "max": 3, "flag": "--reps"}},
                       "desc": "用历史对局重放对比模型 / 参数的速度与合法率（调用较多，会产生费用，耗时数分钟）",
                       "game": False, "cost": True},
}


class Run:
    _ids = itertools.count(1)

    def __init__(self, tool: str, argv: list[str]):
        self.id = next(self._ids)
        self.tool = tool
        self.argv = argv
        self.lines: list[str] = []
        self.started = time.time()
        self.ended: float | None = None
        self.code: int | None = None
        self.proc: subprocess.Popen | None = None

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "tool": self.tool, "running": self.ended is None, "code": self.code,
                "started": self.started, "ended": self.ended, "total": len(self.lines), "lines": self.lines[since:]}


class ToolRunner:
    def __init__(self, root: Path):
        self.root = root
        self.runs: dict[int, Run] = {}
        self.current: Run | None = None
        self.lock = threading.Lock()

    def catalog(self) -> list[dict]:
        out = []
        for tid, t in TOOLS.items():
            img = self.root / t["image"] if t.get("image") else None
            out.append({"id": tid, **{k: v for k, v in t.items() if k != "argv"},
                        "image_at": img.stat().st_mtime if img and img.exists() else None})
        return out

    def build(self, tool: str, params: dict) -> list[str]:
        t = TOOLS.get(tool)
        if t is None:
            raise HTTPException(404, "没有这个工具")
        argv = [sys.executable, "-u", *t["argv"]]
        for name, spec in (t.get("params") or {}).items():
            v = params.get(name, spec.get("default"))
            if v is None:
                continue
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"「{spec['label']}」要是整数") from None
            if not spec.get("min", v) <= v <= spec.get("max", v):
                raise HTTPException(400, f"「{spec['label']}」超出范围")
            if v != spec.get("default") or name == "reps":
                argv += [spec["flag"], str(v)]
        unknown = set(params) - set(t.get("params") or {})
        if unknown:
            raise HTTPException(400, f"不支持的参数：{', '.join(sorted(unknown))}")
        return argv

    def start(self, tool: str, params: dict) -> dict:
        argv = self.build(tool, params)
        with self.lock:
            if self.current and self.current.ended is None:
                raise HTTPException(409, f"「{TOOLS[self.current.tool]['label']}」还在运行")
            run = Run(tool, argv)
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            run.proc = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.runs[run.id] = run
            self.current = run
            for old in sorted(self.runs)[:-20]:   # 只留最近 20 次
                self.runs.pop(old, None)
        threading.Thread(target=self._read, args=(run,), daemon=True, name=f"tool-{tool}").start()
        return run.view()

    def _read(self, run: Run) -> None:
        assert run.proc and run.proc.stdout
        for line in run.proc.stdout:
            if len(run.lines) < MAX_LINES:
                run.lines.append(line.rstrip("\r\n"))
        run.code = run.proc.wait()
        run.ended = time.time()

    def get(self, rid: int) -> Run:
        run = self.runs.get(rid)
        if run is None:
            raise HTTPException(404, "没有这次运行")
        return run

    def stop(self, rid: int) -> None:
        run = self.get(rid)
        if run.proc and run.ended is None:
            run.proc.kill()


class RunBody(BaseModel):
    params: dict = Field(default_factory=dict)


def create_router(runner: ToolRunner) -> APIRouter:
    r = APIRouter(prefix="/api/tools")
    r.get("")(runner.catalog)

    @r.post("/{tool}/run")
    def run(tool: str, body: RunBody):
        return runner.start(tool, body.params)

    @r.get("/runs/{rid}")
    def get_run(rid: int, since: int = 0):
        return runner.get(rid).view(since)

    @r.post("/runs/{rid}/stop")
    def stop(rid: int):
        runner.stop(rid)
        return {"ok": True}

    @r.get("/{tool}/image")
    def image(tool: str):
        t = TOOLS.get(tool)
        if not t or not t.get("image") or not (runner.root / t["image"]).exists():
            raise HTTPException(404, "还没有图片")
        return FileResponse(runner.root / t["image"], headers={"Cache-Control": "no-store"})

    return r

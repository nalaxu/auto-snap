"""机器人子进程管理：启动 `main.py auto --events`（或回放演示），逐行读 stdout 拆成事件 / 原始日志，往 stdin 写控制命令。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from gamelog.live import parse_event

ROOT = Path(__file__).resolve().parents[2]
LOG_LINES = 400          # 原始日志保留行数
HISTORY = 3000           # 本次运行的事件保留条数（新连接的页面用来重建状态）


class BotProcess:
    """同一时刻只跑一个机器人进程。事件经 on_event 回调推出（在读线程里调用）。"""

    def __init__(self, on_event: Callable[[dict], None]):
        self.on_event = on_event
        self.proc: subprocess.Popen | None = None
        self.args: list[str] = []
        self.started_at: float | None = None
        self.options: dict = {}
        self.history: deque[dict] = deque(maxlen=HISTORY)
        self.lock = threading.Lock()
        self._stop_timer: threading.Timer | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {"running": self.running, "pid": self.proc.pid if self.running else None, "options": self.options,
                "started_at": self.started_at, "args": self.args}

    @staticmethod
    def build_args(opts: dict) -> list[str]:
        """面板选项 -> 命令行。demo=对局记录文件名 时启动回放演示而不是真机器人。"""
        py = [sys.executable, "-u"]
        if opts.get("demo"):
            args = py + ["-m", "ui.replay", str(opts["demo"]), "--speed", str(opts.get("speed") or 1.0)]
            if opts.get("loop"):
                args.append("--loop")
            return args
        args = py + [str(ROOT / "main.py"), "auto", "--events"]
        if opts.get("live"):
            args.append("--live")
        if opts.get("client"):
            args += ["--client", str(opts["client"])]
        if opts.get("decider") in ("llm", "rule_based"):
            args += ["--decider", opts["decider"]]
        if int(opts.get("max_games") or 0) > 0:
            args += ["--max-games", str(int(opts["max_games"]))]
        if int(opts.get("max_turns") or 0) > 0:
            args += ["--max-turns", str(int(opts["max_turns"]))]
        if opts.get("skip_current"):
            args.append("--skip-current")
        if opts.get("verbose"):
            args.append("-v")
        return args

    def start(self, opts: dict) -> dict:
        with self.lock:
            if self.running:
                raise RuntimeError("机器人已经在运行")
            self.args = self.build_args(opts)
            self.options = dict(opts)
            self.history.clear()
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self.proc = subprocess.Popen(self.args, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                         bufsize=1, creationflags=flags)
            self.started_at = time.time()
            proc = self.proc
        self._publish({"kind": "process", "state": "started", "pid": proc.pid, "options": self.options})
        threading.Thread(target=self._read, args=(proc,), name="bot-stdout", daemon=True).start()
        return self.status()

    def _publish(self, ev: dict) -> None:
        ev.setdefault("t", round(time.time(), 3))
        self.history.append(ev)
        self.on_event(ev)

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            ev = parse_event(line)
            if ev is None:
                text = line.rstrip("\r\n")
                if text:
                    ev = {"kind": "log", "text": text}
                else:
                    continue
            self._publish(ev)
        code = proc.wait()
        if self._stop_timer:
            self._stop_timer.cancel()
        self._publish({"kind": "process", "state": "exited", "code": code})

    def send(self, cmd: str) -> bool:
        """控制命令写进 stdin（pause / resume / stop）。stop 8 秒内没退出就强制结束。"""
        if not self.running or self.proc is None or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(json.dumps({"cmd": cmd}) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return False
        if cmd == "stop":
            self._stop_timer = threading.Timer(8.0, self.kill)
            self._stop_timer.daemon = True
            self._stop_timer.start()
        return True

    def kill(self) -> bool:
        if not self.running or self.proc is None:
            return False
        self.proc.kill()
        return True

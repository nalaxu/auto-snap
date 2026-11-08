"""知识库 JSON 文件的安全读写：机器人（赛后积累、自动识别）和外部面板（人工编辑）会同时写同一批文件。

update_json(path, fn)：加锁 -> 从磁盘重新读最新内容 -> fn 原地修改 -> 写临时文件后原子替换。
这样任何一方都不会用自己内存里的旧副本覆盖另一方刚写入的修改。
锁是进程内锁 + 同目录 .lock 文件（跨进程，短暂持有；超时后仍继续，避免死锁卡住对局）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(path.resolve()), threading.Lock())


class _FileLock:
    def __init__(self, path: Path, timeout: float = 3.0):
        self.lock_path = path.with_name(path.name + ".lock")
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self):
        end = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:   # 残留的锁（进程崩溃）：超过 30 秒视为失效
                    if time.time() - self.lock_path.stat().st_mtime > 30:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > end:
                    return self
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    for attempt in range(5):   # Windows 上目标文件被短暂占用时重试
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def update_json(path: Path, fn: Callable[[Any], Any], default: Any = None) -> Any:
    """读最新 -> fn(data) 原地修改（也可返回新对象）-> 原子写回。返回写入后的数据。"""
    with _thread_lock(path), _FileLock(path):
        data = read_json(path, default)
        out = fn(data)
        if out is not None:
            data = out
        write_json(path, data)
        return data

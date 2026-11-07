"""游戏客户端配置档：把「连哪个客户端」集中到一处，感知 / 执行 / 卡组读取都从这里取参数。

同一款游戏有多个客户端（国服 Steam 渠道、国服官网网易账号版、国际服），它们的差异集中在：
- 进程名、安装目录（两个国服版可能都叫 SnapCN.exe → 用安装目录里的标志文件区分）
- Unity 数据目录（卡组状态文件 PlayState.json / CollectionState.json 所在）
- 界面模板（不同语言 / 版本的按钮图片）
- 资料区域（cn：人工维护的国服平衡覆盖生效；global：只用国际服资料库）

配置在 config/settings.toml 的 [game] 与 [game.clients.<key>]；当前客户端按以下顺序决定：
命令行 --client > 环境变量 SNAP_CLIENT > [game].client。值为 "auto" 时选第一个正在运行的客户端。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
SHARED_TEMPLATES = ROOT / "data" / "templates" / "ui"
LOCALLOW = Path(os.environ.get("USERPROFILE", "~")) / "AppData" / "LocalLow"

# 没有 [game.clients] 时的内置默认值（与 config/settings.toml 保持一致）
BUILTIN: dict[str, dict] = {
    "cn_steam": {
        "label": "国服 · Steam 渠道",
        "process_name": "SnapCN.exe",
        "steam_app_id": 2955370,
        "require_files": ["NtUniSdkSteam.dll"],
        "data_dir": "%USERPROFILE%/AppData/LocalLow/NetEase/SnapCN",
        "region": "cn",
        "verified": True,
    },
    "cn_official": {
        "label": "国服 · 官网（网易账号）",
        "process_name": "SnapCN.exe",
        "exclude_files": ["NtUniSdkSteam.dll"],
        "data_dir": "%USERPROFILE%/AppData/LocalLow/NetEase/SnapCN",
        "region": "cn",
        "verified": True,
    },
    "global": {
        "label": "国际服 · Steam",
        "process_name": "SNAP.exe",
        "steam_app_id": 1997040,
        "data_dir": "%USERPROFILE%/AppData/LocalLow/Second Dinner/SNAP",
        "states_subdir": "Standalone/States/nvprod",
        "region": "global",
        "verified": False,
    },
}


def process_images() -> list[tuple[int, str]]:
    """本机进程 -> [(pid, exe 完整路径)]。EnumProcesses + QueryFullProcessImageNameW 约 20ms；
    psutil.process_iter(["name"]) 在本机要 ~7s（会拖慢每次附加游戏进程）。"""
    import ctypes as C
    import ctypes.wintypes as W

    k32, psapi = C.WinDLL("kernel32", use_last_error=True), C.WinDLL("psapi")
    arr = (W.DWORD * 4096)()
    needed = W.DWORD()
    if not psapi.EnumProcesses(arr, C.sizeof(arr), C.byref(needed)):
        return [(p.pid, p.info.get("exe") or "") for p in psutil.process_iter(["exe"])]
    k32.OpenProcess.restype = W.HANDLE
    out = []
    buf = C.create_unicode_buffer(1024)
    for pid in arr[: needed.value // C.sizeof(W.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        size = W.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, C.byref(size)):
            out.append((int(pid), buf.value))
        k32.CloseHandle(h)
    return out


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))) if p else Path()


@dataclass(frozen=True)
class ClientProfile:
    key: str
    label: str
    process_name: str
    data_dir: Path
    install_dir: Path | None = None          # 填了则只认这个目录下的 exe
    require_files: tuple[str, ...] = ()       # exe 同目录下必须存在的文件（区分同名进程的不同渠道）
    exclude_files: tuple[str, ...] = ()       # exe 同目录下不能存在的文件
    states_subdir: str = "Standalone/States/prod"
    templates_dir: Path | None = None         # 本客户端专用界面模板；缺的按钮回落到共享模板
    region: str = "cn"
    steam_app_id: int | None = None
    verified: bool = False                    # 内存结构 / 界面是否已在该客户端实测通过
    extra: dict = field(default_factory=dict, compare=False)

    # ------------------------------------------------------------ 路径
    @property
    def states_dir(self) -> Path:
        return self.data_dir / self.states_subdir

    @property
    def template_dirs(self) -> list[Path]:
        dirs = [self.templates_dir] if self.templates_dir else []
        return dirs + [SHARED_TEMPLATES]

    # ------------------------------------------------------------ 进程匹配
    def matches_exe(self, name: str, exe: str | None) -> bool:
        """name / exe：进程名与完整路径（路径拿不到时只按进程名和 install_dir 以外的条件判断）。"""
        if (name or "").lower() != self.process_name.lower():
            return False
        if not exe:
            # 权限不足拿不到路径：只有不需要按目录区分时才算匹配
            return not (self.install_dir or self.require_files or self.exclude_files)
        folder = Path(exe).parent
        if self.install_dir and folder.resolve() != self.install_dir.resolve():
            return False
        if any(not (folder / f).exists() for f in self.require_files):
            return False
        if any((folder / f).exists() for f in self.exclude_files):
            return False
        return True

    def matches_pid(self, pid: int) -> bool:
        exe = next((e for p, e in process_images() if p == pid), None)
        if exe:
            return self.matches_exe(Path(exe).name, exe)
        try:
            p = psutil.Process(pid)
            name = p.name()
        except (psutil.Error, OSError):
            return False
        try:
            exe = p.exe()
        except (psutil.Error, OSError):
            exe = None
        return self.matches_exe(name, exe)

    def find_pids(self) -> list[int]:
        return [pid for pid, exe in process_images() if self.matches_exe(Path(exe).name, exe)]

    def running(self) -> bool:
        return bool(self.find_pids())

    def describe(self) -> str:
        tag = "" if self.verified else "（未实测）"
        return f"{self.label} [{self.key}] {self.process_name}{tag}"


def _profile(key: str, raw: dict) -> ClientProfile:
    known = {"label", "process_name", "data_dir", "install_dir", "require_files", "exclude_files", "states_subdir",
             "templates_dir", "region", "steam_app_id", "verified"}
    tdir = raw.get("templates_dir") or ""
    if tdir and not Path(tdir).is_absolute():
        tdir = str(ROOT / tdir)
    return ClientProfile(
        key=key,
        label=raw.get("label", key),
        process_name=raw.get("process_name", "SnapCN.exe"),
        data_dir=_expand(raw.get("data_dir", "")) or LOCALLOW / "NetEase" / "SnapCN",
        install_dir=_expand(raw["install_dir"]) if raw.get("install_dir") else None,
        require_files=tuple(raw.get("require_files") or ()),
        exclude_files=tuple(raw.get("exclude_files") or ()),
        states_subdir=raw.get("states_subdir", "Standalone/States/prod"),
        templates_dir=_expand(tdir) if tdir else None,
        region=raw.get("region", "cn"),
        steam_app_id=raw.get("steam_app_id"),
        verified=bool(raw.get("verified", False)),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def profiles(settings: dict | None = None) -> dict[str, ClientProfile]:
    """全部客户端配置档：内置默认值被 [game.clients.<key>] 逐项覆盖。"""
    game = (settings or {}).get("game", {}) or {}
    merged = {k: dict(v) for k, v in BUILTIN.items()}
    for k, v in (game.get("clients") or {}).items():
        merged.setdefault(k, {}).update(v or {})
    return {k: _profile(k, v) for k, v in merged.items()}


def selected_key(settings: dict | None = None, override: str | None = None) -> str:
    game = (settings or {}).get("game", {}) or {}
    return override or os.environ.get("SNAP_CLIENT") or game.get("client") or "cn_steam"


def resolve(settings: dict | None = None, override: str | None = None) -> ClientProfile:
    """当前要连接的客户端。"auto"：第一个正在运行的；都没运行则用 [game].default_client（默认 cn_steam）。"""
    all_ = profiles(settings)
    key = selected_key(settings, override)
    if key == "auto":
        for p in all_.values():
            if p.running():
                return p
        key = ((settings or {}).get("game", {}) or {}).get("default_client", "cn_steam")
    if key not in all_:
        raise ValueError(f"未知客户端 {key!r}，可选：{', '.join(all_)} 或 auto")
    return all_[key]

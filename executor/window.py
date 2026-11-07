"""定位游戏窗口客户区（屏幕物理像素）。需先调用 mouse.enable_dpi_awareness()。"""
from __future__ import annotations

import time

import psutil
import win32api
import win32con
import win32gui
import win32process

from .layout import Rect


class WindowNotFound(RuntimeError):
    pass


_cache: dict = {}


def _proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return ""


def find_window(process_name="SnapCN.exe") -> int:
    """定位游戏主窗口。process_name：进程名或 clients.ClientProfile。
    缓存句柄，仅在失效时重新查找（遍历全部进程在本机需 ~7s，不能每次都做）。"""
    client = process_name if hasattr(process_name, "matches_pid") else None
    if client:
        process_name = client.process_name
    cache_key = client.key if client else process_name
    h = _cache.get(cache_key)
    if h and win32gui.IsWindow(h) and win32gui.IsWindowVisible(h):
        return h
    found: list[tuple[int, int]] = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h) or win32gui.GetClassName(h) != "UnityWndClass":
            return True
        pid = win32process.GetWindowThreadProcessId(h)[1]
        if _proc_name(pid).lower() != process_name.lower():
            return True
        if client and not client.matches_pid(pid):   # 同名进程的其他渠道客户端
            return True
        if win32gui.IsIconic(h):   # 最小化时客户区为 0，仍算找到（需要操作时 bring_to_front 会还原）
            found.append((1, h))
        else:
            l, t, r, b = win32gui.GetClientRect(h)
            if r - l > 400 and b - t > 300:
                found.append(((r - l) * (b - t), h))
        return True

    win32gui.EnumWindows(cb, None)
    if not found:
        _cache.pop(cache_key, None)
        label = client.describe() if client else process_name
        raise WindowNotFound(f"{label} 没有可见的游戏窗口（未运行或已关闭）")
    _cache[cache_key] = max(found)[1]
    return _cache[cache_key]


def client_rect(hwnd: int) -> Rect:
    l, t, r, b = win32gui.GetClientRect(hwnd)
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    return Rect(x, y, r - l, b - t)


def is_foreground(hwnd: int) -> bool:
    return win32gui.GetForegroundWindow() == hwnd


def bring_to_front(hwnd: int) -> bool:
    """切到前台。Windows 禁止后台进程随意抢焦点：先直接尝试，失败则模拟一次 Alt 键（常用做法）再试。"""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    for use_alt in (False, True):
        if use_alt:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass
        time.sleep(0.15)
        if is_foreground(hwnd):
            return True
    return False


def wait_foreground(hwnd: int, timeout_s: float = 120) -> bool:
    """自动切换失败时，提示玩家点一下游戏窗口，等待其获得焦点。"""
    if is_foreground(hwnd) or bring_to_front(hwnd):
        return True
    print("  ⏸ 无法自动切换到游戏窗口，请点一下游戏窗口（程序等待中）")
    end = time.time() + timeout_s
    while time.time() < end:
        time.sleep(0.5)
        if is_foreground(hwnd):
            print("  ▶ 游戏窗口已在前台，继续")
            time.sleep(0.5)
            return True
    return False

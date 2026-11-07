"""鼠标控制：Win32 SendInput 的平滑拖拽 + 点击（纯 ctypes）。

坐标为屏幕物理像素；调用前先 enable_dpi_awareness()，否则高 DPI 下会错位。
"""
from __future__ import annotations

import ctypes
import random
import time
from ctypes import wintypes

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
VK_F8 = 0x77


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.windll.user32


class Aborted(RuntimeError):
    """用户按下 F8 中止。"""


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            _user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def abort_pressed() -> bool:
    return bool(_user32.GetAsyncKeyState(VK_F8) & 0x8000)


def check_abort() -> None:
    if abort_pressed():
        raise Aborted("F8 中止")


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    vx, vy = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN), _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return round((x - vx) * 65535 / max(vw - 1, 1)), round((y - vy) * 65535 / max(vh - 1, 1))


def _send(flags: int, x: int | None = None, y: int | None = None) -> None:
    dx = dy = 0
    if x is not None and y is not None:
        dx, dy = _to_absolute(x, y)
        flags |= MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inp = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(dx, dy, 0, flags, 0, 0)))
    if _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def move(pos: tuple[int, int]) -> None:
    _send(0, *pos)


def path(start: tuple[int, int], end: tuple[int, int], steps: int) -> list[tuple[int, int]]:
    """ease-in-out 插值路径，终点精确。"""
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)
        pts.append((round(start[0] + (end[0] - start[0]) * e), round(start[1] + (end[1] - start[1]) * e)))
    return pts


def drag(start: tuple[int, int], end: tuple[int, int], steps: int = 18, step_delay: float = 0.012, hold_s: float = 0.12) -> None:
    """按下 -> 停顿（让游戏拾起卡牌）-> 平滑移动 -> 停顿 -> 松开。任何情况下都会松开左键。"""
    check_abort()
    move(start)
    time.sleep(0.05)
    _send(MOUSEEVENTF_LEFTDOWN)
    try:
        time.sleep(hold_s)
        for p in path(start, end, steps):
            move(p)
            time.sleep(step_delay + random.uniform(0, 0.004))
        time.sleep(hold_s)
    finally:
        _send(MOUSEEVENTF_LEFTUP)


def click(pos: tuple[int, int], hold_s: float = 0.06) -> None:
    check_abort()
    move(pos)
    time.sleep(0.05)
    _send(MOUSEEVENTF_LEFTDOWN)
    try:
        time.sleep(hold_s)
    finally:
        _send(MOUSEEVENTF_LEFTUP)


_VK = {"esc": 0x1B, "enter": 0x0D, "space": 0x20}


def key(name: str, hold_s: float = 0.05) -> None:
    """按一次键（esc / enter / space）。"""
    import win32api
    import win32con

    vk = _VK[name]
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(hold_s)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def press(pos: tuple[int, int], hold_s: float = 1.2) -> None:
    """长按（激活场上牌）。"""
    check_abort()
    move(pos)
    time.sleep(0.05)
    _send(MOUSEEVENTF_LEFTDOWN)
    end = time.time() + hold_s
    while time.time() < end:
        time.sleep(0.05)
        if abort_pressed():
            break
    _send(MOUSEEVENTF_LEFTUP)

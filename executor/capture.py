"""截取游戏窗口画面（PrintWindow：窗口被遮挡也能截到，不抢焦点）。用于标定检查与失败现场留证。"""
from __future__ import annotations

import ctypes

import win32gui
import win32ui
from PIL import Image


def capture_window(hwnd: int, w: int, h: int) -> Image.Image | None:
    """PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)。失败返回 None。"""
    hdc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x1 | 0x2)
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
    if not ok or img.getextrema() == ((0, 0), (0, 0), (0, 0)):
        return None
    return img

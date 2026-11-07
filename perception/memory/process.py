"""Windows 进程只读访问：OpenProcess(VM_READ | QUERY_INFORMATION) + ReadProcessMemory。

不写内存、不注入、不挂钩子、不调整权限；也不做任何规避检测的处理。
"""
from __future__ import annotations

import bisect
import ctypes as C
import ctypes.wintypes as W
import struct
from collections.abc import Iterable, Iterator

import numpy as np
import psutil

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000

PAGE_GUARD = 0x100
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}   # R, RW, WC, XR, XRW, XWC
WRITABLE = {0x04, 0x08, 0x40, 0x80}

CHUNK = 16 * 1024 * 1024


class MBI(C.Structure):  # MEMORY_BASIC_INFORMATION64
    _fields_ = [
        ("BaseAddress", C.c_ulonglong),
        ("AllocationBase", C.c_ulonglong),
        ("AllocationProtect", W.DWORD),
        ("PartitionId", W.WORD),
        ("RegionSize", C.c_ulonglong),
        ("State", W.DWORD),
        ("Protect", W.DWORD),
        ("Type", W.DWORD),
    ]


_k32 = C.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
_k32.OpenProcess.restype = W.HANDLE
_k32.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
_k32.ReadProcessMemory.restype = W.BOOL
_k32.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
_k32.VirtualQueryEx.restype = C.c_size_t
_k32.CloseHandle.argtypes = [W.HANDLE]


class ProcessNotFound(RuntimeError):
    pass


class Process:
    def __init__(self, name="SnapCN.exe"):
        """name：进程名，或 clients.ClientProfile（按进程名 + 安装目录标志文件区分同名的不同渠道客户端）。"""
        if hasattr(name, "find_pids"):
            pids = name.find_pids()
            label = name.describe()
        else:
            from clients import process_images

            pids = [pid for pid, exe in process_images() if exe.replace("/", "\\").rsplit("\\", 1)[-1].lower() == name.lower()]
            label = name
        if not pids:
            raise ProcessNotFound(f"{label} 未运行")
        self.pid = pids[0]
        self.handle = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self.pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ 基础读取
    def read(self, addr: int, size: int) -> bytes | None:
        if addr <= 0x10000 or size <= 0:
            return None
        buf = C.create_string_buffer(size)
        got = C.c_size_t()
        if not _k32.ReadProcessMemory(self.handle, C.c_void_p(addr), buf, size, C.byref(got)) or got.value != size:
            return None
        return buf.raw

    def ptr(self, addr: int) -> int:
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0] if b else 0

    def i32(self, addr: int) -> int | None:
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def u32(self, addr: int) -> int | None:
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def i64(self, addr: int) -> int | None:
        b = self.read(addr, 8)
        return struct.unpack("<q", b)[0] if b else None

    def u8(self, addr: int) -> int | None:
        b = self.read(addr, 1)
        return b[0] if b else None

    def f32(self, addr: int) -> float | None:
        b = self.read(addr, 4)
        return struct.unpack("<f", b)[0] if b else None

    def cstr(self, addr: int, maxlen: int = 256) -> str | None:
        b = self.read(addr, maxlen)
        if b is None:  # 可能跨页尾，退而求其次
            b = self.read(addr, 32)
            if b is None:
                return None
        end = b.find(b"\0")
        if end < 0:
            return None
        try:
            return b[:end].decode("utf-8")
        except UnicodeDecodeError:
            return None

    # ------------------------------------------------------------ 内存区域
    def regions(self, types: Iterable[int] = (MEM_PRIVATE,), writable: bool | None = True) -> Iterator[tuple[int, int, int]]:
        """枚举已提交、可读的区域 -> (base, size, type)。"""
        types = set(types)
        mbi = MBI()
        addr = 0
        while _k32.VirtualQueryEx(self.handle, C.c_void_p(addr), C.byref(mbi), C.sizeof(mbi)):
            base, size = mbi.BaseAddress, mbi.RegionSize
            prot = mbi.Protect & 0xFF
            if (
                mbi.State == MEM_COMMIT
                and mbi.Type in types
                and not (mbi.Protect & PAGE_GUARD)
                and prot in READABLE
                and (writable is None or (prot in WRITABLE) == writable)
            ):
                yield base, size, mbi.Type
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt

    def region_base(self, addr: int) -> int | None:
        """addr 所在内存区域（与 regions() 枚举结果一致）的基址，用于记住「热区域」，下次优先只扫这些区域。"""
        for _ in range(2):
            regs = getattr(self, "_region_list", None)
            if regs:
                i = bisect.bisect_right(regs, (addr, float("inf"))) - 1
                if i >= 0 and regs[i][0] <= addr < regs[i][0] + regs[i][1]:
                    return regs[i][0]
            self._region_list = sorted((b, s) for b, s, _ in self.regions())   # 新分配的区域：刷新一次
        return None

    def _chunks(self, types, writable, overlap: int = 0, bases: set[int] | None = None) -> Iterator[tuple[int, bytes]]:
        for base, size, _ in self.regions(types, writable):
            if bases is not None and base not in bases:
                continue
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                data = self.read(base + off, n)
                if data is not None:
                    yield base + off, data
                off += n - overlap if n == CHUNK and overlap else n

    def find_bytes(self, needle: bytes, types=(MEM_PRIVATE,), writable: bool | None = None, limit: int = 1000) -> list[int]:
        hits: list[int] = []
        for addr, data in self._chunks(types, writable, overlap=len(needle)):
            i = data.find(needle)
            while i >= 0:
                hits.append(addr + i)
                if len(hits) >= limit:
                    return hits
                i = data.find(needle, i + 1)
        return sorted(set(hits))

    def find_u64(self, values: Iterable[int], types=(MEM_PRIVATE,), writable: bool | None = True,
                 bases: set[int] | None = None) -> dict[int, list[int]]:
        """一次扫描查找多个 8 字节对齐的指针值 -> {value: [地址...]}。bases：只扫这些区域（热区域快速扫描）。"""
        vals = np.array(sorted(set(values)), dtype="<u8")
        out: dict[int, list[int]] = {int(v): [] for v in vals}
        for addr, data in self._chunks(types, writable, bases=bases):
            arr = np.frombuffer(data[: len(data) // 8 * 8], dtype="<u8")
            idx = np.flatnonzero(np.isin(arr, vals))
            for i in idx:
                out[int(arr[i])].append(addr + int(i) * 8)
        return out

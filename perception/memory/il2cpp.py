"""IL2CPP 运行时解析（只读）：按名字找类、按名字取字段偏移、读 string/List/Array/Dictionary。

与 Snapper 硬编码 RVA/偏移不同，这里全部在运行时解析，游戏小版本更新后大概率无需改代码：
  1. 在内存中定位 global-metadata.dat 映射（magic 0xFAB11BAF），在其字符串表里找到类名 "Foo\\0"
  2. Il2CppClass.name (+0x10) 指向该字符串 -> 扫描堆中指向它的指针得到 Il2CppClass 候选
  3. 校验命名空间 (+0x18)；字段表指针所在槽位通过 "FieldInfo.parent == klass" 自动探测
  4. FieldInfo = {name*, type*, parent*, int32 offset, uint32 token}，共 0x20 字节
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .process import MEM_MAPPED, MEM_PRIVATE, Process

METADATA_MAGIC = b"\xaf\x1b\xb1\xfa"
CLS_NAME, CLS_NS, CLS_PARENT = 0x10, 0x18, 0x58
FIELD_INFO_SIZE = 0x20

# Il2CppTypeEnum
T_BOOL, T_CHAR, T_I1, T_U1, T_I2, T_U2, T_I4, T_U4, T_I8, T_U8, T_R4, T_R8, T_STRING = range(0x02, 0x0F)
T_VALUETYPE, T_CLASS, T_ARRAY, T_GENERICINST, T_OBJECT, T_SZARRAY = 0x11, 0x12, 0x14, 0x15, 0x1C, 0x1D
REF_TYPES = {T_STRING, T_CLASS, T_ARRAY, T_GENERICINST, T_OBJECT, T_SZARRAY}
ATTR_STATIC, ATTR_LITERAL = 0x10, 0x40


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    type_enum: int
    attrs: int
    owner: str

    @property
    def is_static(self) -> bool:
        return bool(self.attrs & (ATTR_STATIC | ATTR_LITERAL))


class Il2Cpp:
    def __init__(self, proc: Process):
        self.p = proc
        self._fields_slot: int | None = None
        self._field_cache: dict[int, dict[str, Field]] = {}
        self._name_cache: dict[int, tuple[str, str]] = {}
        self._strings: tuple[int, int] | None = None

    # ------------------------------------------------------------ 元数据字符串表
    def metadata_strings(self) -> tuple[int, int]:
        """返回内存中 metadata 字符串表的 (起始地址, 长度)。"""
        if self._strings:
            return self._strings
        for base, size, _ in self.p.regions(types=(MEM_MAPPED, MEM_PRIVATE), writable=None):
            head = self.p.read(base, 32)
            if head and head[:4] == METADATA_MAGIC and 24 <= struct.unpack_from("<i", head, 4)[0] <= 40:
                str_off, str_size = struct.unpack_from("<ii", head, 24)
                if 0 < str_off < size and str_size > 0:
                    self._strings = (base + str_off, str_size)
                    return self._strings
        raise RuntimeError("内存中未找到 global-metadata.dat 映射")

    def _string_addrs(self, names: list[str]) -> dict[str, list[int]]:
        start, size = self.metadata_strings()
        out = {n: [] for n in names}
        off = 0
        while off < size:
            n = min(16 * 1024 * 1024, size - off)
            data = self.p.read(start + off, n + 128) or self.p.read(start + off, n)
            if data is None:
                break
            for name in names:
                needle = b"\0" + name.encode() + b"\0"
                i = data.find(needle)
                while 0 <= i < n:
                    out[name].append(start + off + i + 1)
                    i = data.find(needle, i + 1)
            off += n
        return out

    # ------------------------------------------------------------ 类
    def class_name(self, klass: int) -> tuple[str, str]:
        """-> (namespace, name)；非法指针返回 ("", "")。"""
        if klass in self._name_cache:
            return self._name_cache[klass]
        name = self.p.cstr(self.p.ptr(klass + CLS_NAME), 128) or ""
        ns = self.p.cstr(self.p.ptr(klass + CLS_NS), 128) or ""
        if name:
            self._name_cache[klass] = (ns, name)
        return ns, name

    def object_class_name(self, obj: int) -> str:
        return self.class_name(self.p.ptr(obj))[1] if obj else ""

    def find_classes(self, wanted: list[tuple[str, str | None]]) -> dict[tuple[str, str | None], list[int]]:
        """一次扫描定位多个类。wanted: [(name, namespace 或 None)] -> {key: [klass...]}。"""
        names = sorted({n for n, _ in wanted})
        str_addrs = self._string_addrs(names)
        all_ptrs = [a for v in str_addrs.values() for a in v]
        hits = self.p.find_u64(all_ptrs) if all_ptrs else {}
        out: dict[tuple[str, str | None], list[int]] = {k: [] for k in wanted}
        for name, ns in wanted:
            for s in str_addrs[name]:
                for h in hits.get(s, []):
                    klass = h - CLS_NAME
                    kns, kname = self.class_name(klass)
                    if kname != name or (ns is not None and kns != ns):
                        continue
                    if not self.p.ptr(klass):  # image 指针
                        continue
                    out[(name, ns)].append(klass)
        for k in out:
            # 优先：字段表可解析的那个
            out[k] = sorted(set(out[k]), key=lambda c: -len(self.fields(c)))
        return out

    def check_classes(self, found: dict) -> bool:
        """缓存的类地址是否仍然有效（同一进程内 klass 地址不变）。"""
        for (name, ns), ks in found.items():
            for k in ks:
                kns, kname = self.class_name(k)
                if kname != name or (ns is not None and kns != ns):
                    return False
        return True

    # ------------------------------------------------------------ 字段
    def _probe_fields_slot(self, klass: int) -> int | None:
        for slot in range(0x60, 0x120, 8):
            fp = self.p.ptr(klass + slot)
            if fp and self.p.ptr(fp + 0x10) == klass and self.p.cstr(self.p.ptr(fp), 128):
                return slot
        return None

    def own_fields(self, klass: int) -> list[Field]:
        if self._fields_slot is None:
            slot = self._probe_fields_slot(klass)
            if slot is None:
                return []
            self._fields_slot = slot
        fp = self.p.ptr(klass + self._fields_slot)
        if not fp:
            return []
        owner = self.class_name(klass)[1]
        out = []
        for i in range(1024):
            e = self.p.read(fp + i * FIELD_INFO_SIZE, FIELD_INFO_SIZE)
            if not e:
                break
            name_p, type_p, parent, offset = struct.unpack_from("<QQQi", e)
            if parent != klass:
                break
            name = self.p.cstr(name_p, 128)
            bits = self.p.u32(type_p + 8) if type_p else None
            if name is None or bits is None:
                break
            out.append(Field(name, offset, (bits >> 16) & 0xFF, bits & 0xFFFF, owner))
        return out

    def fields(self, klass: int) -> dict[str, Field]:
        """含父类字段；子类同名字段优先。"""
        if klass in self._field_cache:
            return self._field_cache[klass]
        result: dict[str, Field] = {}
        k, depth = klass, 0
        while k and depth < 16:
            for f in self.own_fields(k):
                result.setdefault(f.name, f)
            k = self.p.ptr(k + CLS_PARENT)
            depth += 1
        if result:
            self._field_cache[klass] = result
        return result

    def field(self, obj: int, *names: str) -> Field | None:
        """按名字取字段，自动尝试 <Name>k__BackingField 形式。"""
        fs = self.fields(self.p.ptr(obj)) if obj else {}
        for n in names:
            f = fs.get(n) or fs.get(f"<{n}>k__BackingField")
            if f and not f.is_static:
                return f
        return None

    # ------------------------------------------------------------ 取值
    def get_ptr(self, obj: int, *names: str) -> int:
        f = self.field(obj, *names)
        return self.p.ptr(obj + f.offset) if f else 0

    def get_int(self, obj: int, *names: str) -> int | None:
        f = self.field(obj, *names)
        if not f:
            return None
        if f.type_enum in (T_I8, T_U8):
            return self.p.i64(obj + f.offset)
        if f.type_enum in (T_BOOL, T_I1, T_U1):
            return self.p.u8(obj + f.offset)
        return self.p.i32(obj + f.offset)

    def get_bool(self, obj: int, *names: str) -> bool | None:
        f = self.field(obj, *names)
        return None if not f else bool(self.p.u8(obj + f.offset))

    def get_str(self, obj: int, *names: str) -> str | None:
        return self.string(self.get_ptr(obj, *names))

    def string(self, s: int) -> str | None:
        if not s:
            return None
        n = self.p.i32(s + 0x10)
        if n is None or not 0 <= n <= 4096:
            return None
        b = self.p.read(s + 0x14, n * 2) if n else b""
        return b.decode("utf-16le", "replace") if b is not None else None

    def array(self, arr: int, elem_size: int = 8, limit: int = 4096) -> tuple[int, int]:
        """-> (数据起始地址, 长度)"""
        if not arr:
            return 0, 0
        n = self.p.i64(arr + 0x18) or 0
        return arr + 0x20, max(0, min(n, limit))

    def list_ptrs(self, lst: int, limit: int = 4096) -> list[int]:
        """List<引用类型> 的元素指针。"""
        if not lst:
            return []
        items = self.get_ptr(lst, "_items") or self.p.ptr(lst + 0x10)
        size = self.get_int(lst, "_size")
        size = self.p.i32(lst + 0x18) if size is None else size
        if not items or size is None or not 0 <= size <= limit:
            return []
        data, cap = self.array(items)
        size = min(size, cap)
        raw = self.p.read(data, size * 8) if size else b""
        return list(struct.unpack(f"<{size}Q", raw)) if raw else []

    def list_ints(self, lst: int, limit: int = 4096) -> list[int]:
        if not lst:
            return []
        items = self.get_ptr(lst, "_items") or self.p.ptr(lst + 0x10)
        size = self.get_int(lst, "_size")
        size = self.p.i32(lst + 0x18) if size is None else size
        if not items or size is None or not 0 <= size <= limit:
            return []
        data, cap = self.array(items)
        size = min(size, cap)
        raw = self.p.read(data, size * 4) if size else b""
        return list(struct.unpack(f"<{size}i", raw)) if raw else []

    def dict_values_int_key(self, d: int, limit: int = 8192) -> list[tuple[int, int]]:
        """Dictionary<int, 引用类型> -> [(key, value_ptr)]。Entry = {int hash, int next, int key, pad, ptr value}。"""
        if not d:
            return []
        entries = self.get_ptr(d, "_entries", "entries") or self.p.ptr(d + 0x18)
        count = self.get_int(d, "_count", "count")
        count = self.p.i32(d + 0x20) if count is None else count
        if not entries or count is None or not 0 <= count <= limit:
            return []
        data, cap = self.array(entries)
        count = min(count, cap)
        raw = self.p.read(data, count * 24) if count else b""
        out = []
        for i in range(count if raw else 0):
            h, nxt, key, _pad, val = struct.unpack_from("<iiiiQ", raw, i * 24)
            # 新版 corefx：hashCode 为 uint，空闲槽 next <= -2；旧版 Mono：空闲槽 value 被清零
            if nxt >= -1 and val:
                out.append((key, val))
        return out

    # ------------------------------------------------------------ 调试
    def describe(self, obj: int) -> dict:
        """浅层 dump：标量字段给值，引用字段给类名（不递归）。仅供调试类结构。"""
        out: dict = {"@class": ".".join(filter(None, self.class_name(self.p.ptr(obj))))}
        for f in sorted(self.fields(self.p.ptr(obj)).values(), key=lambda f: f.offset):
            if f.is_static:
                continue
            a = obj + f.offset
            if f.type_enum == T_BOOL:
                v = bool(self.p.u8(a))
            elif f.type_enum in (T_I4, T_U4, T_I2, T_U2, T_VALUETYPE):
                v = self.p.i32(a)
            elif f.type_enum in (T_I8, T_U8):
                v = self.p.i64(a)
            elif f.type_enum == T_R4:
                v = self.p.f32(a)
            elif f.type_enum == T_STRING:
                v = self.string(self.p.ptr(a))
            elif f.type_enum in REF_TYPES:
                ptr = self.p.ptr(a)
                v = f"<{self.object_class_name(ptr)}>" if ptr else None
            else:
                v = f"<type 0x{f.type_enum:x}>"
            out[f"{f.name}@0x{f.offset:x}"] = v
        return out

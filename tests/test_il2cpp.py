"""用内存中伪造的 IL2CPP 结构测试解析逻辑（不需要游戏进程）。"""
import struct

from perception.memory.il2cpp import T_I4, T_STRING, Il2Cpp

BASE = 0x1000_0000


class FakeProcess:
    def __init__(self, size=0x10000):
        self.mem = bytearray(size)

    def put(self, addr, data):
        o = addr - BASE
        self.mem[o:o + len(data)] = data

    def read(self, addr, size):
        o = addr - BASE
        return bytes(self.mem[o:o + size]) if 0 <= o and o + size <= len(self.mem) else None

    def ptr(self, a):
        b = self.read(a, 8)
        return struct.unpack("<Q", b)[0] if b else 0

    def i32(self, a):
        b = self.read(a, 4)
        return struct.unpack("<i", b)[0] if b else None

    def u32(self, a):
        b = self.read(a, 4)
        return struct.unpack("<I", b)[0] if b else None

    def i64(self, a):
        b = self.read(a, 8)
        return struct.unpack("<q", b)[0] if b else None

    def u8(self, a):
        b = self.read(a, 1)
        return b[0] if b else None

    def cstr(self, a, maxlen=256):
        b = self.read(a, maxlen) or self.read(a, 16)
        return b[: b.find(b"\0")].decode() if b and b"\0" in b else None


def make_class(p, klass, name, fields, strings_at, types_at):
    """fields: [(name, offset, type_enum)]；字段表放在 klass+0x80。"""
    p.put(strings_at, name.encode() + b"\0")
    p.put(klass, struct.pack("<Q", 0xDEAD))                 # image
    p.put(klass + 0x10, struct.pack("<Q", strings_at))       # name
    p.put(klass + 0x18, struct.pack("<Q", strings_at + len(name)))  # ns -> ""
    fi = klass + 0x200
    p.put(klass + 0x80, struct.pack("<Q", fi))
    s = strings_at + 0x40
    for i, (fname, off, te) in enumerate(fields):
        p.put(s, fname.encode() + b"\0")
        t = types_at + i * 0x10
        p.put(t + 8, struct.pack("<I", te << 16))
        p.put(fi + i * 0x20, struct.pack("<QQQi", s, t, klass, off))
        s += 0x40


def test_fields_list_string_dict():
    p = FakeProcess()
    il = Il2Cpp(p)
    obj_k, list_k, dict_k, str_k = BASE + 0x100, BASE + 0x1000, BASE + 0x2000, BASE + 0x3000
    make_class(p, obj_k, "Game", [("<Turn>k__BackingField", 0x10, T_I4), ("Id", 0x18, T_STRING), ("_players", 0x20, 0x15)],
               BASE + 0x4000, BASE + 0x5000)
    make_class(p, list_k, "List`1", [("_items", 0x10, 0x1D), ("_size", 0x18, T_I4)], BASE + 0x4400, BASE + 0x5400)
    make_class(p, dict_k, "Dictionary`2", [("_entries", 0x18, 0x1D), ("_count", 0x20, T_I4)], BASE + 0x4800, BASE + 0x5800)

    obj, s, lst, arr, d, ents = (BASE + x for x in (0x6000, 0x6100, 0x6200, 0x6300, 0x6400, 0x6500))
    p.put(obj, struct.pack("<Q", obj_k))
    p.put(obj + 0x10, struct.pack("<i", 4))
    p.put(obj + 0x18, struct.pack("<Q", s))
    p.put(obj + 0x20, struct.pack("<Q", lst))
    p.put(s, struct.pack("<QQi", str_k, 0, 3) + "回合A".encode("utf-16le"))
    p.put(lst, struct.pack("<QQQi", list_k, 0, arr, 2))
    p.put(arr + 0x18, struct.pack("<q", 4))
    p.put(arr + 0x20, struct.pack("<QQ", 0xAAA0, 0xBBB0))
    p.put(d, struct.pack("<QQQQi", dict_k, 0, 0, ents, 3))
    p.put(ents + 0x18, struct.pack("<q", 3))
    p.put(ents + 0x20, struct.pack("<iiiiQ", 11, -1, 7, 0, 0xC0) + struct.pack("<iiiiQ", 0, -3, 0, 0, 0)
          + struct.pack("<iiiiQ", 12, 0, 9, 0, 0xD0))

    assert il.get_int(obj, "Turn") == 4                  # 自动匹配 k__BackingField
    assert il.get_str(obj, "Id") == "回合A"
    assert il.list_ptrs(il.get_ptr(obj, "_players")) == [0xAAA0, 0xBBB0]
    assert il.dict_values_int_key(d) == [(7, 0xC0), (9, 0xD0)]  # 跳过空闲槽
    assert il.class_name(obj_k) == ("", "Game")

#!/usr/bin/env python3
"""Static checks for the prebuilt Minecraft jar (no JDK required).

Two JVM-level mistakes in the shared-memory overlay have shipped before, and both take the game
down on the first client tick, long before anything gameplay-related runs:

  1. ``MethodHandles.byteBufferViewVarHandle(int.class, ...)`` - a view handle needs the *array*
     class (``int[].class``). With a primitive class the static initializer throws
     ``IllegalArgumentException: not an array: int``, which surfaces as
     ``ExceptionInInitializerError`` inside ``BorderCraftMod.tryOpen`` and crashes Minecraft on
     launch.
  2. Calling that view handle with a ``long`` byte offset - the access mode type is
     ``(ByteBuffer, int)``, so a ``long`` argument makes every access throw
     ``WrongMethodTypeException`` (it cannot adapt ``long`` to ``int``).

This tool reads the class files straight out of the jar and fails if either pattern is present,
so a stale or hand-patched artifact cannot go back out the door. It needs only Python; run it
after building or replacing ``release/bordercraft-0.1.0.jar``:

    python3 tools/jar_check.py [jar ...]
"""
from __future__ import annotations

import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_JARS = [os.path.join(ROOT, "release", "bordercraft-0.1.0.jar")]
TARGET_PREFIX = "dev/bordercraft/link/SharedMemory"

# VarHandle access modes that read or write an int in a ByteBuffer view.
ACCESS_MODES = {
    "get", "set", "getVolatile", "setVolatile", "getOpaque", "setOpaque", "getAcquire",
    "setRelease", "getAndSet", "getAndAdd", "compareAndSet", "compareAndExchange",
    "getAndAddAcquire", "getAndAddRelease", "getAndBitwiseOr", "getAndBitwiseAnd",
    "getAndBitwiseXor", "weakCompareAndSet",
}

# opcode -> instruction length in bytes; 0 means variable length (handled separately)
LENGTHS: dict[int, int] = {0xAA: 0, 0xAB: 0, 0xC4: 0}
for _op in range(0x00, 0x10):
    LENGTHS[_op] = 1
LENGTHS.update({0x10: 2, 0x11: 3, 0x12: 2, 0x13: 3, 0x14: 3})
for _op in range(0x15, 0x1A):
    LENGTHS[_op] = 2
for _op in range(0x1A, 0x36):
    LENGTHS[_op] = 1
for _op in range(0x36, 0x3B):
    LENGTHS[_op] = 2
for _op in range(0x3B, 0x57):
    LENGTHS[_op] = 1
for _op in range(0x57, 0x84):
    LENGTHS[_op] = 1
LENGTHS[0x84] = 3
for _op in range(0x85, 0x99):
    LENGTHS[_op] = 1
for _op in range(0x99, 0xAA):
    LENGTHS[_op] = 3
LENGTHS[0xA9] = 2
for _op in range(0xAC, 0xB2):
    LENGTHS[_op] = 1
for _op in range(0xB2, 0xB9):
    LENGTHS[_op] = 3
LENGTHS[0xB9] = 5
LENGTHS[0xBA] = 5
LENGTHS.update({0xBB: 3, 0xBC: 2, 0xBD: 3, 0xBE: 1, 0xBF: 1, 0xC0: 3, 0xC1: 3,
                0xC2: 1, 0xC3: 1, 0xC5: 4, 0xC6: 3, 0xC7: 3, 0xC8: 5, 0xC9: 5})
for _op in range(0xCA, 0x100):
    LENGTHS.setdefault(_op, 1)

WRAPPER_TYPES = {"java/lang/Integer", "java/lang/Long", "java/lang/Short", "java/lang/Byte",
                 "java/lang/Character", "java/lang/Float", "java/lang/Double", "java/lang/Boolean",
                 "java/lang/Void"}


class ClassFormatError(Exception):
    pass


class Klass:
    """Just enough of a .class parser to walk method bytecode."""

    def __init__(self, data: bytes):
        self.data = data
        self.pool: list = [None]
        self.pool_end = self._parse_constant_pool()
        self.methods = self._parse_methods(self._after_header())

    # -- raw readers ---------------------------------------------------------------------
    def u1(self, off: int) -> int:
        return self.data[off]

    def u2(self, off: int) -> int:
        return struct.unpack_from(">H", self.data, off)[0]

    def u4(self, off: int) -> int:
        return struct.unpack_from(">I", self.data, off)[0]

    # -- constant pool -------------------------------------------------------------------
    def _parse_constant_pool(self) -> int:
        off = 10
        count = self.u2(8)
        i = 1
        while i < count:
            tag = self.u1(off)
            off += 1
            if tag == 1:  # Utf8 (keep the numeric tag so accessors can compare pools uniformly)
                ln = self.u2(off)
                self.pool.append((1, self.data[off + 2:off + 2 + ln].decode("utf-8", "replace")))
                off += 2 + ln
            elif tag in (7, 8, 16, 19, 20):  # Class, String, MethodType, Module, Package
                self.pool.append((tag, self.u2(off)))
                off += 2
            elif tag in (3, 4):  # Integer, Float
                self.pool.append((tag, self.u4(off)))
                off += 4
            elif tag in (5, 6):  # Long, Double occupy two entries
                self.pool.append((tag, struct.unpack_from(">q", self.data, off)[0]))
                self.pool.append(None)
                off += 8
                i += 1
            elif tag in (9, 10, 11, 12, 17, 18):  # refs, NameAndType, dynamic
                self.pool.append((tag, self.u2(off), self.u2(off + 2)))
                off += 4
            elif tag == 15:  # MethodHandle
                self.pool.append((tag, self.u1(off), self.u2(off + 1)))
                off += 3
            else:
                raise ClassFormatError(f"unknown constant pool tag {tag} at {off - 1}")
            i += 1
        return off

    # -- structure ------------------------------------------------------------------------
    def _after_header(self) -> int:
        off = self.pool_end + 6  # access_flags, this_class, super_class
        off += 2 + self.u2(off) * 2  # interfaces
        field_count = self.u2(off)  # fields
        off += 2
        for _ in range(field_count):
            off += 6  # access_flags, name_index, descriptor_index
            off = self._skip_attributes(off)
        return off

    def _skip_attributes(self, off: int) -> int:
        """Skip an attribute table; `off` points at the attribute count."""
        count = self.u2(off)
        off += 2
        for _ in range(count):
            off += 6 + self.u4(off + 2)  # name_index, length, info
        return off

    def _parse_methods(self, off: int):
        out = []
        method_count = self.u2(off)
        off += 2
        for _ in range(method_count):
            name = self.utf(self.u2(off + 2))
            desc = self.utf(self.u2(off + 4))
            off += 6
            attrs = self.u2(off)
            off += 2
            code = None
            for _ in range(attrs):
                aname = self.utf(self.u2(off))
                alen = self.u4(off + 2)
                if aname == "Code":
                    body = off + 6
                    code_len = self.u4(body + 4)
                    code = self.data[body + 8:body + 8 + code_len]
                off += 6 + alen
            out.append((name, desc, code))
        return out

    def utf(self, idx: int) -> str:
        e = self.pool[idx]
        if not e or e[0] != 1:
            raise ClassFormatError(f"entry {idx} is not utf8")
        return e[1]

    def class_name(self, idx: int) -> str:
        e = self.pool[idx]
        if not e or e[0] != 7:
            raise ClassFormatError(f"entry {idx} is not a class")
        return self.utf(e[1])

    def member_ref(self, idx: int) -> tuple[str, str, str]:
        e = self.pool[idx]
        if not e or e[0] not in (9, 10, 11):
            raise ClassFormatError(f"entry {idx} is not a member reference")
        name, desc = self.pool[e[2]][1], self.pool[e[2]][2]
        return self.class_name(e[1]), self.utf(name), self.utf(desc)


def instructions(code: bytes):
    """Yield (offset, opcode) for every instruction in a Code attribute's byte array."""
    p, n = 0, len(code)
    while p < n:
        op = code[p]
        yield p, op
        ln = LENGTHS.get(op, 1)
        if op == 0xAA:  # tableswitch
            pad = (4 - ((p + 1) % 4)) % 4
            low = struct.unpack_from(">i", code, p + 1 + pad + 4)[0]
            high = struct.unpack_from(">i", code, p + 1 + pad + 8)[0]
            ln = 1 + pad + 12 + 4 * (high - low + 1)
        elif op == 0xAB:  # lookupswitch
            pad = (4 - ((p + 1) % 4)) % 4
            npairs = struct.unpack_from(">i", code, p + 1 + pad + 4)[0]
            ln = 1 + pad + 8 + 8 * npairs
        elif op == 0xC4:  # wide
            ln = 6 if code[p + 1] == 0x84 else 4
        p += ln


class Bytecode:
    """Reads instruction operands out of a Code attribute's byte array (not the class file)."""

    def __init__(self, code: bytes):
        self.code = code

    def u1(self, off: int) -> int:
        return self.code[off]

    def u2(self, off: int) -> int:
        return struct.unpack_from(">H", self.code, off)[0]


def check_class(name: str, data: bytes) -> list[str]:
    problems: list[str] = []
    k = Klass(data)
    for mname, _mdesc, code in k.methods:
        if code is None:
            continue
        bc = Bytecode(code)
        calls_view_handle = False
        have_array_class = False
        for off, op in instructions(code):
            try:
                if op in (0x12, 0x13):  # ldc / ldc_w
                    idx = bc.u1(off + 1) if op == 0x12 else bc.u2(off + 1)
                    e = k.pool[idx]
                    if e and e[0] == 7 and k.class_name(idx).startswith("["):
                        have_array_class = True
                elif op == 0xB2:  # getstatic
                    owner, fname, fdesc = k.member_ref(bc.u2(off + 1))
                    if fname == "TYPE" and fdesc == "Ljava/lang/Class;" and owner in WRAPPER_TYPES:
                        problems.append(
                            f"{name}.{mname}: view handle built from {owner.replace('/', '.')}.TYPE, "
                            f"a primitive class - the static initializer throws 'not an array'")
                elif op in (0xB6, 0xB7, 0xB8):  # invokevirtual / invokespecial / invokestatic
                    owner, iname, idesc = k.member_ref(bc.u2(off + 1))
                    if owner == "java/lang/invoke/MethodHandles" and iname == "byteBufferViewVarHandle":
                        calls_view_handle = True
                    if owner == "java/lang/invoke/VarHandle" and iname in ACCESS_MODES:
                        params = idesc[1:idesc.index(")")]
                        if params.startswith("Ljava/nio/ByteBuffer;") and not \
                                params[len("Ljava/nio/ByteBuffer;"):].startswith("I"):
                            problems.append(
                                f"{name}.{mname}: VarHandle.{iname}{idesc} takes a non-int index - "
                                f"a long byte offset throws WrongMethodTypeException on every access")
            except (ClassFormatError, IndexError, struct.error) as e:
                problems.append(f"{name}.{mname}: could not parse bytecode at offset {off}: {e}")
                break
        if calls_view_handle and not have_array_class:
            problems.append(
                f"{name}.{mname}: byteBufferViewVarHandle is called without an array class "
                f"constant (expected int[].class)")
    return problems


def check_jar(path: str) -> list[str]:
    if not os.path.isfile(path):
        return [f"{path}: not found (build the jar first)"]
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist()
                       if n.startswith(TARGET_PREFIX) and n.endswith(".class"))
        if not names:
            return [f"{path}: no {TARGET_PREFIX}*.class entries"]
        problems: list[str] = []
        for n in names:
            problems.extend(check_class(n[:-len(".class")], z.read(n)))
    return problems


def main(argv: list[str]) -> int:
    failed = False
    for jar in argv[1:] or DEFAULT_JARS:
        problems = check_jar(jar)
        if problems:
            failed = True
            print(f"FAIL {jar}")
            for p in problems:
                print(f"      {p}")
        else:
            print(f"ok   {jar} (SharedMemory view-handle calls are well formed)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

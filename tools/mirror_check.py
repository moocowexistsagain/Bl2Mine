#!/usr/bin/env python3
"""Verify the Java protocol mirror matches the Python mirror (and thus the .h source of truth).

Parses constant declarations out of fabric/src/main/java/dev/bordercraft/link/Proto.java and
compares them to the Python module. Run after any protocol change:
    python3 tools/mirror_check.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "protocol", "python"))
import bordercraft_protocol as P  # noqa: E402

JAVA = os.path.join(HERE, "..", "fabric", "src", "main", "java", "dev", "bordercraft", "link", "Proto.java")

# Python attr -> Java const name
CHECKS = {
    "MAGIC": "MAGIC",
    "VERSION": "VERSION",
    "UNITS_PER_BLOCK": "UNITS_PER_BLOCK",
    "OFF_HEADER": "OFF_HEADER",
    "OFF_BL2_STATE": "OFF_BL2_STATE",
    "OFF_MC_STATE": "OFF_MC_STATE",
    "OFF_OVERLAY_CTL": "OFF_OVERLAY_CTL",
    "OFF_OVERLAY_SLOT_HDR": "OFF_OVERLAY_SLOT_HDR",
    "OFF_WATER_GRID": "OFF_WATER_GRID",
    "OFF_INPUT_RING": "OFF_INPUT_RING",
    "OFF_COLLISION_RING": "OFF_COLLISION_RING",
    "OFF_ACTOR_TABLE": "OFF_ACTOR_TABLE",
    "OFF_EVENT_RING": "OFF_EVENT_RING",
    "OFF_OVERLAY_PIXELS": "OFF_OVERLAY_PIXELS",
    "MAX_OVERLAY_W": "MAX_OVERLAY_W",
    "MAX_OVERLAY_H": "MAX_OVERLAY_H",
    "OVERLAY_SLOT_BYTES": "OVERLAY_SLOT_BYTES",
    "OVERLAY_SLOTS": "OVERLAY_SLOTS",
    "MAPPING_BYTES": "MAPPING_BYTES",
    "INPUT_RING_ENTRIES": "INPUT_RING_ENTRIES",
    "COLLISION_RING_ENTRIES": "COLLISION_RING_ENTRIES",
    "COLLISION_REC_BYTES": "COLLISION_REC_BYTES",
    "MAX_ACTORS": "MAX_ACTORS",
    "EVENT_RING_ENTRIES": "EVENT_RING_ENTRIES",
    "MC_STATE_LEN": "MC_STATE_BYTES",  # special: computed below
    "BL2_STATE_LEN": "BL2_STATE_BYTES",
    "NO_WATER": "NO_WATER",
}


def java_constants(path: str) -> dict[str, float]:
    src = open(path, encoding="utf-8").read()
    out = {}
    pat = re.compile(
        r"public\s+static\s+final\s+(?:int|long|double|float)\s+(\w+)\s*=\s*([^;]+);"
    )
    decls = []
    for name, expr in pat.findall(src):
        expr = expr.split("//")[0].strip()
        expr = expr.replace("(long)", "").replace("(int)", "")
        expr = re.sub(r"(?<=\d)[fFdD]\b", "", expr)  # strip Java float/double literal suffixes
        expr = re.sub(r"\b0x([0-9A-Fa-f]+)", lambda m: str(int(m.group(1), 16)), expr)
        decls.append((name, expr))

    # resolve identifier references iteratively (declarations can reference earlier constants)
    for _ in range(8):
        progress = False
        remaining = []
        for name, expr in decls:
            resolved = expr
            for k, v in out.items():
                resolved = re.sub(rf"\b{k}\b", repr(v), resolved)
            if re.fullmatch(r"[\d\s\*\+\-\.eE']+", resolved):
                try:
                    out[name] = float(eval(resolved, {"__builtins__": {}}))
                    progress = True
                    continue
                except Exception:
                    pass
            remaining.append((name, expr))
        decls = remaining
        if not decls:
            break
        if not progress:
            break
    return out


def main() -> int:
    jc = java_constants(JAVA)
    py_len = {
        "MC_STATE_LEN": __import__("struct").calcsize(P._MC_FMT),
        "BL2_STATE_LEN": __import__("struct").calcsize(P._BL2_FMT),
    }
    fails = 0
    for py_name, java_name in CHECKS.items():
        py_val = py_len.get(py_name, getattr(P, py_name, None))
        if py_val is None:
            print(f"MISSING python attr {py_name}")
            fails += 1
            continue
        if java_name not in jc:
            print(f"MISSING java const {java_name}")
            fails += 1
            continue
        if abs(float(py_val) - jc[java_name]) > 1e-6:
            print(f"MISMATCH {py_name}: python={py_val} java({java_name})={jc[java_name]}")
            fails += 1
    if fails:
        print(f"mirror_check: {fails} failure(s)")
        return 1
    print(f"mirror_check: OK ({len(CHECKS)} constants match)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

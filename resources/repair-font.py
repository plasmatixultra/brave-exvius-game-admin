#!/usr/bin/env python3
"""Repair finalf.ttf so browsers will load it.

"Final Fantasy Font v0.5" (Juan Pablo Reyes Altamirano, 1999) ships a cmap table whose
Macintosh subtable is declared with length 0. Chrome's OTS rejects the whole cmap --
"OTS parsing error: cmap: Bad cmap subtable" -- and silently falls back to a system serif,
which is why the font appears not to load.

This drops the broken (1,0) subtable, keeps the valid Windows (3,1) format-4 one, rebuilds
the table directory and recomputes every checksum (including head.checkSumAdjustment).
No glyphs are changed.

    python3 resources/repair-font.py resources/finalf.ttf resources/finalf-fixed.ttf
"""
import math
import struct
import sys


def checksum(data: bytes) -> int:
    if len(data) % 4:
        data += b"\0" * (4 - len(data) % 4)
    return sum(struct.unpack(">%dI" % (len(data) // 4), data)) & 0xFFFFFFFF


def repair(src: str, dst: str) -> None:
    raw = open(src, "rb").read()
    if struct.unpack(">I", raw[:4])[0] != 0x00010000:
        raise SystemExit("not a TrueType font")
    count = struct.unpack(">H", raw[4:6])[0]
    tables = {}
    for i in range(count):
        off = 12 + i * 16
        tag, _chk, start, length = struct.unpack(">4sIII", raw[off:off + 16])
        tables[tag] = bytearray(raw[start:start + length])

    cmap = bytes(tables[b"cmap"])
    _ver, n = struct.unpack(">HH", cmap[:4])
    keep = None
    for i in range(n):
        pid, eid, off = struct.unpack(">HHI", cmap[4 + i * 8:12 + i * 8])
        if (pid, eid) == (3, 1):
            length = struct.unpack(">H", cmap[off + 2:off + 4])[0]
            keep = cmap[off:off + length]
    if keep is None:
        raise SystemExit("no Windows (3,1) cmap subtable to keep")
    tables[b"cmap"] = bytearray(struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + keep)

    struct.pack_into(">I", tables[b"head"], 8, 0)  # zeroed while assembling

    tags = sorted(tables)
    num = len(tags)
    sel = int(math.floor(math.log2(num)))
    search = (2 ** sel) * 16
    header = struct.pack(">IHHHH", 0x00010000, num, search, sel, num * 16 - search)

    offset = 12 + 16 * num
    entries, body = [], b""
    for tag in tags:
        data = bytes(tables[tag])
        entries.append(struct.pack(">4sIII", tag, checksum(data), offset + len(body), len(data)))
        body += data + b"\0" * ((4 - len(data) % 4) % 4)
    font = header + b"".join(entries) + body

    head_off = next(o for tag, _c, o, _l in (struct.unpack(">4sIII", e) for e in entries) if tag == b"head")
    out = bytearray(font)
    struct.pack_into(">I", out, head_off + 8, (0xB1B0AFBA - checksum(font)) & 0xFFFFFFFF)
    open(dst, "wb").write(bytes(out))
    print(f"{dst}: {len(out)} bytes, cmap {len(tables[b'cmap'])} bytes")


if __name__ == "__main__":
    repair(sys.argv[1], sys.argv[2])

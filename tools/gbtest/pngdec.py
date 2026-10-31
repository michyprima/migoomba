"""Minimal PNG decoder (non-interlaced; gray, RGB, palette, RGBA; 1/2/4/8 bit). Returns (w, h, rows of RGB tuples)."""
import struct, zlib


def decode(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, idat, plte = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif typ == b"IDAT":
            idat += body
    assert interlace == 0, "interlaced PNG not supported"
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, channels * depth // 8)
    stride = (w * channels * depth + 7) // 8
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if depth == 8:
            px = [tuple(line[x * channels:(x + 1) * channels]) for x in range(w)]
        else:
            per = 8 // depth
            mask = (1 << depth) - 1
            px = [((line[x // per] >> (8 - depth * (x % per + 1))) & mask,) for x in range(w)]
        out = []
        for p in px:
            if ctype == 3:
                out.append(plte[p[0]])
            elif ctype in (0, 4):
                v = p[0] * 255 // ((1 << depth) - 1)
                out.append((v, v, v))
            else:
                out.append(p[:3])
        rows.append(out)
    return w, h, rows

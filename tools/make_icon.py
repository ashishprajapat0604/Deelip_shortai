#!/usr/bin/env python3
"""Generate assets/shortsai.ico + assets/shortsai.png with no third-party deps.

Pure stdlib (zlib/struct) so it can be regenerated anywhere. Draws a rounded
square with a warm gradient and a white play triangle, supersampled 4x for
clean edges.
"""
import os
import zlib
import struct

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
os.makedirs(OUT_DIR, exist_ok=True)

# Warm sunset gradient (matches the app's pink/amber UI accents).
C1 = (255, 92, 122)    # pink
C2 = (255, 176, 92)    # amber
FG = (255, 255, 255)   # play triangle


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _in_rounded_rect(x, y, w, h, r):
    if x < r and y < r:
        return (x - r) ** 2 + (y - r) ** 2 <= r * r
    if x > w - r and y < r:
        return (x - (w - r)) ** 2 + (y - r) ** 2 <= r * r
    if x < r and y > h - r:
        return (x - r) ** 2 + (y - (h - r)) ** 2 <= r * r
    if x > w - r and y > h - r:
        return (x - (w - r)) ** 2 + (y - (h - r)) ** 2 <= r * r
    return True


def _in_triangle(px, py, a, b, c):
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1 = sign((px, py), a, b)
    d2 = sign((px, py), b, c)
    d3 = sign((px, py), c, a)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def render(size, ss=4):
    """Return RGBA bytes for one `size`x`size` icon."""
    W = H = size * ss
    r = W * 0.22
    # Play triangle: centred, pointing right.
    cx, cy = W * 0.5, H * 0.5
    tw = W * 0.30           # half-width
    th = H * 0.32           # half-height
    a = (cx - tw * 0.75, cy - th)
    b = (cx - tw * 0.75, cy + th)
    cpt = (cx + tw, cy)

    # Supersampled buffer -> averaged down into the final pixels.
    acc = [[[0, 0, 0, 0] for _ in range(size)] for _ in range(size)]
    for yy in range(H):
        oy = yy // ss
        for xx in range(W):
            ox = xx // ss
            if not _in_rounded_rect(xx, yy, W, H, r):
                continue
            if _in_triangle(xx, yy, a, b, cpt):
                col = FG
            else:
                col = _lerp(C1, C2, (xx + yy) / float(W + H))
            cell = acc[oy][ox]
            cell[0] += col[0]
            cell[1] += col[1]
            cell[2] += col[2]
            cell[3] += 255

    n = ss * ss
    out = bytearray()
    for row in acc:
        for px in row:
            alpha = px[3] // n
            if alpha == 0:
                out += bytes((0, 0, 0, 0))
            else:
                # Un-weight colour by coverage so edges don't darken toward black.
                cov = px[3] / 255.0
                out += bytes((
                    min(255, int(px[0] / cov)) if cov else 0,
                    min(255, int(px[1] / cov)) if cov else 0,
                    min(255, int(px[2] / cov)) if cov else 0,
                    alpha,
                ))
    return bytes(out)


def png_bytes(size, rgba):
    """Minimal RGBA PNG encoder."""
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                                  # filter: None
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main():
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        print(f"  rendering {s}x{s} …")
        images.append((s, png_bytes(s, render(s))))

    # ── ICO: header + one directory entry per size + PNG payloads ──
    ico = bytearray(struct.pack("<HHH", 0, 1, len(images)))     # reserved, type=icon, count
    offset = 6 + 16 * len(images)
    for s, data in images:
        ico += struct.pack(
            "<BBBBHHII",
            0 if s >= 256 else s,   # width  (0 means 256)
            0 if s >= 256 else s,   # height
            0, 0,                   # palette, reserved
            1, 32,                  # colour planes, bits per pixel
            len(data), offset,
        )
        offset += len(data)
    for _s, data in images:
        ico += data

    ico_path = os.path.join(OUT_DIR, "shortsai.ico")
    with open(ico_path, "wb") as f:
        f.write(ico)
    print(f"  wrote {ico_path}  ({len(ico)//1024} KB, {len(images)} sizes)")

    # Standalone 256px PNG for the Linux .desktop icon.
    png_path = os.path.join(OUT_DIR, "shortsai.png")
    with open(png_path, "wb") as f:
        f.write(dict(images)[256])
    print(f"  wrote {png_path}")


if __name__ == "__main__":
    main()

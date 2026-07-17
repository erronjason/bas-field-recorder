"""Generate assets/icon.ico from the BAS three-bar mark.

Mirrors _draw_mark() geometry in recorder/icons.py exactly.
Writes a PNG-compressed multi-size ICO (Windows Vista+ format).
Run directly or via build.ps1 before PyInstaller.
"""

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = [16, 32, 48, 256]

_TOP = (0xBA, 0x58, 0x1C)   # #BA581C
_MID = (0xC9, 0x74, 0x0E)   # #C9740E
_BOT = (0xF9, 0xED, 0xD9)   # #F9EDD9


def _draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    top_h = max(1, round(size * 10 / 32))
    gap   = max(1, round(size * 1.5 / 32))
    mid_h = max(1, size - 2 * top_h - 2 * gap)
    mid_w = size * 3 // 4
    y_mid = top_h + gap
    y_bot = y_mid + mid_h + gap

    d.rectangle([0, 0,     size - 1,  top_h - 1],         fill=_TOP)
    d.rectangle([0, y_mid, mid_w - 1, y_mid + mid_h - 1], fill=_MID)
    d.rectangle([0, y_bot, size - 1,  y_bot + top_h - 1], fill=_BOT)

    return img


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_ico(path: Path, sizes: list[int]) -> None:
    """Write a multi-size ICO using PNG-compressed entries (Vista+)."""
    chunks = [_png_bytes(_draw(s)) for s in sizes]
    n = len(sizes)

    # ICO file layout: 6-byte header + n*16-byte directory + image data
    header = struct.pack("<HHH", 0, 1, n)   # reserved=0, type=1 (ICO), count
    dir_offset = 6 + n * 16
    data_offset = dir_offset
    directory = b""
    for i, size in enumerate(sizes):
        w = size if size < 256 else 0   # ICO encodes 256 as 0
        h = size if size < 256 else 0
        # width, height, palette_count, reserved, planes, bit_depth, data_size, offset
        directory += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(chunks[i]), data_offset)
        data_offset += len(chunks[i])

    path.write_bytes(header + directory + b"".join(chunks))


def main() -> None:
    out = Path(__file__).parent / "assets" / "icon.ico"
    out.parent.mkdir(exist_ok=True)
    save_ico(out, SIZES)
    print(f"Written: {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

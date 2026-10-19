"""Write packaging/icon.ico. Original artwork: a slate tile with one blue bar."""

from __future__ import annotations

import struct
from pathlib import Path


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    margin = max(1, size // 8)
    if x < margin or y < margin or x >= size - margin or y >= size - margin:
        return (23, 29, 37, 255)
    bar_top = size // 2 - max(1, size // 16)
    bar_bottom = bar_top + max(2, size // 8)
    if bar_top <= y < bar_bottom and margin + size // 6 <= x < size - margin - size // 6:
        return (76, 141, 255, 255)
    return (23, 29, 37, 255)


def _dib(size: int) -> bytes:
    header = struct.pack(
        "<IIIHHIIIIII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        size * size * 4,
        0,
        0,
        0,
        0,
    )
    xor_rows = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            red, green, blue, alpha = _pixel(x, y, size)
            xor_rows += bytes((blue, green, red, alpha))
    mask_row = (size + 31) // 32 * 4
    mask = bytes(mask_row * size)
    return header + bytes(xor_rows) + mask


def write_icon(path: Path) -> None:
    sizes = (16, 32, 48)
    images = [_dib(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = bytearray()
    offset = 6 + 16 * len(sizes)
    blobs = bytearray()
    for size, image in zip(sizes, images):
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(image),
            offset,
        )
        offset += len(image)
        blobs += image
    path.write_bytes(header + bytes(entries) + bytes(blobs))


if __name__ == "__main__":
    destination = Path(__file__).with_name("icon.ico")
    write_icon(destination)
    print(destination)

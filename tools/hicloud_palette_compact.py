#!/usr/bin/env python3
"""Inspect and compact HiCloud's embedded 8-bpp TIM without changing layout."""

from __future__ import annotations

import argparse
import hashlib
import struct
from collections import Counter
from pathlib import Path

from PIL import Image

from hicloud_clut_patch import lzs_compress, lzs_decompress


EXPECTED_LZS_SHA256 = "3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4"
EXPECTED_DEC_SIZE = 161768
TIM_OFFSET = 0x167F4
CLUT_RECT = (320, 192, 256, 1)
IMAGE_RECT = (320, 0, 64, 192)
DEFAULT_CLUT_WIDTH = 240
DISC_ALLOCATION = 0x18800


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_tim(dec: bytes):
    t = TIM_OFFSET
    if struct.unpack_from("<2I", dec, t) != (0x10, 9):
        raise SystemExit("error: expected HiCloud 8-bpp TIM not found at 0x167F4")
    clut_size = struct.unpack_from("<I", dec, t + 8)[0]
    clut_rect = struct.unpack_from("<4H", dec, t + 12)
    valid_rect = (clut_rect[0], clut_rect[1], clut_rect[3]) == (320, 192, 1)
    if not valid_rect or not (16 <= clut_rect[2] <= 256) or clut_size != 524:
        raise SystemExit(f"error: unexpected CLUT block: size={clut_size}, rect={clut_rect}")
    palette = list(struct.unpack_from("<256H", dec, t + 20))
    image_off = t + 8 + clut_size
    image_size = struct.unpack_from("<I", dec, image_off)[0]
    image_rect = struct.unpack_from("<4H", dec, image_off + 4)
    if image_rect != IMAGE_RECT or image_size != 24588:
        raise SystemExit(f"error: unexpected image block: size={image_size}, rect={image_rect}")
    pixels_off = image_off + 12
    pixels = dec[pixels_off:pixels_off + 128 * 192]
    if len(pixels) != 128 * 192:
        raise SystemExit("error: truncated HiCloud pixel data")
    return palette, pixels_off, pixels


def psx_rgba(word: int) -> tuple[int, int, int, int]:
    # Preserve PSX zero as transparent in the diagnostic PNG. Bit 15 has GPU
    # blending semantics rather than ordinary PNG alpha, so show it opaque.
    if word == 0:
        return (0, 0, 0, 0)
    return tuple(((word >> shift) & 31) * 255 // 31 for shift in (0, 5, 10)) + (255,)


def nearest_compatible_index(palette: list[int], source_index: int, limit: int) -> int:
    source = palette[source_index]
    srgb = psx_rgba(source)[:3]
    sbit = source & 0x8000
    candidates = [i for i in range(limit) if (palette[i] & 0x8000) == sbit]
    if not candidates:
        candidates = list(range(limit))
    return min(candidates, key=lambda i: (
        sum((a - b) ** 2 for a, b in zip(srgb, psx_rgba(palette[i])[:3])), i
    ))


def checked_decompress(path: Path, require_original: bool = False) -> tuple[bytes, bytes]:
    wrapped = path.read_bytes()
    if require_original and sha256(wrapped) != EXPECTED_LZS_SHA256:
        raise SystemExit(f"error: unexpected source SHA-256: {sha256(wrapped)}")
    dec = lzs_decompress(wrapped)
    if len(dec) != EXPECTED_DEC_SIZE:
        raise SystemExit(f"error: unexpected decompressed size: {len(dec)}")
    parse_tim(dec)
    return wrapped, dec


def unpack(source: Path, output: Path) -> None:
    _, dec = checked_decompress(source)
    output.write_bytes(dec)
    print(f"wrote {output} ({len(dec)} bytes, sha256 {sha256(dec)})")


def export_png(source: Path, output: Path) -> None:
    _, dec = checked_decompress(source)
    palette, _, pixels = parse_tim(dec)
    image = Image.new("RGBA", (128, 192))
    image.putdata([psx_rgba(palette[index]) for index in pixels])
    image.save(output)
    print(f"wrote {output} (diagnostic 128x192 texture atlas)")


def compact(source: Path, output: Path, png: Path | None, width: int) -> None:
    if not (16 <= width <= 256) or width % 16:
        raise SystemExit("error: --width must be a multiple of 16 from 16 through 256")
    wrapped, original = checked_decompress(source, require_original=True)
    dec = bytearray(original)
    palette, pixels_off, pixels = parse_tim(dec)
    original_palette = palette[:]
    index_counts = Counter(pixels)
    color_counts: Counter[int] = Counter()
    for index, count in index_counts.items():
        color_counts[palette[index]] += count
    retained = set(color_counts)

    def rgb5(word: int) -> tuple[int, int, int]:
        return tuple((word >> shift) & 31 for shift in (0, 5, 10))

    def color_distance(a: int, b: int) -> int:
        ar, ag, ab = rgb5(a); br, bg, bb = rgb5(b)
        return (ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2

    # If more unique colors are used than fit, discard the colors with the
    # lowest frequency-weighted nearest-neighbor error. Preserve transparent
    # zero and never merge across the PSX STP (bit-15) boundary.
    while len(retained) > width:
        choices = []
        for color in retained:
            if color == 0:
                continue
            peers = [other for other in retained
                     if other not in (color, 0) and ((other ^ color) & 0x8000) == 0]
            if not peers:
                continue
            nearest = min(peers, key=lambda other: (color_distance(color, other), other))
            choices.append((color_counts[color] * color_distance(color, nearest),
                            color_counts[color], color, nearest))
        if not choices:
            raise SystemExit("error: cannot compact further without changing STP semantics")
        retained.remove(min(choices)[2])

    color_mapping: dict[int, int] = {}
    for color in color_counts:
        if color in retained:
            color_mapping[color] = color
        else:
            peers = [other for other in retained
                     if other != 0 and ((other ^ color) & 0x8000) == 0]
            color_mapping[color] = min(peers,
                key=lambda other: (color_distance(color, other), other))

    # Assign each retained color to one slot below the requested width. Reuse
    # an existing matching slot when possible, then consume any remaining slot.
    destination: dict[int, int] = {}
    occupied: set[int] = set()
    color_order = sorted(retained, key=lambda color: min(
        index for index, word in enumerate(original_palette) if word == color))
    for color in color_order:
        existing = next((index for index in range(width)
                         if index not in occupied and original_palette[index] == color), None)
        if existing is None:
            existing = next(index for index in range(width) if index not in occupied)
        destination[color] = existing
        occupied.add(existing)
        palette[existing] = color
        struct.pack_into("<H", dec, TIM_OFFSET + 20 + existing * 2, color)

    mapping = {
        index: destination[color_mapping[original_palette[index]]]
        for index in index_counts
    }
    quantized_pixels = 0
    max_rgb5_error = 0
    for offset, index in enumerate(pixels):
        source_color = original_palette[index]
        target_color = color_mapping[source_color]
        if source_color != target_color:
            quantized_pixels += 1
            max_rgb5_error = max(max_rgb5_error, color_distance(source_color, target_color))
        dec[pixels_off + offset] = mapping[index]

    # Upload only palette entries 0..239. Keep the original 524-byte CLUT
    # block and all following offsets intact; the final 16 words remain inert.
    struct.pack_into("<H", dec, TIM_OFFSET + 16, width)

    packed = lzs_compress(bytes(dec))
    if len(packed) > DISC_ALLOCATION:
        raise SystemExit(
            f"error: compressed result {len(packed)} exceeds allocation {DISC_ALLOCATION}"
        )
    # Preserve the original ISO file length for simple in-place replacement.
    if len(packed) > len(wrapped):
        raise SystemExit(
            f"error: result grew to {len(packed)} bytes (original {len(wrapped)})"
        )
    result = packed + bytes(len(wrapped) - len(packed))
    if lzs_decompress(result) != bytes(dec):
        raise SystemExit("error: recompressed LZS failed exact round-trip validation")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    print(f"wrote {output}")
    print(f"CLUT upload width: 256 -> {width}")
    changed = {a: b for a, b in mapping.items() if a != b}
    print("pixel-index remaps: " + (", ".join(f"{a}->{b}" for a, b in sorted(changed.items())) or "none"))
    print(f"unique colors: {len(color_counts)} -> {len(retained)}; "
          f"quantized pixels: {quantized_pixels}; max RGB5 squared error: {max_rgb5_error}")
    print(f"LZS payload/file size: {len(packed)}/{len(result)} bytes")
    print(f"sha256: {sha256(result)}")
    if png:
        export_png(output, png)


def verify(source: Path) -> None:
    wrapped, dec = checked_decompress(source)
    palette, _, pixels = parse_tim(dec)
    width = struct.unpack_from("<H", dec, TIM_OFFSET + 16)[0]
    invalid = sorted(index for index in set(pixels) if index >= width)
    recompressed = lzs_compress(dec)
    if lzs_decompress(recompressed) != dec:
        raise SystemExit("error: LZS recompression round trip failed")
    print(f"valid: {source}")
    print(f"file/decompressed size: {len(wrapped)}/{len(dec)}")
    print(f"CLUT upload width: {width}; referenced indices outside width: {invalid}")
    print(f"palette entries parsed: {len(palette)}; sha256: {sha256(wrapped)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("unpack"); p.add_argument("input", type=Path); p.add_argument("output", type=Path)
    p = sub.add_parser("png"); p.add_argument("input", type=Path); p.add_argument("output", type=Path)
    p = sub.add_parser("compact"); p.add_argument("input", type=Path); p.add_argument("output", type=Path); p.add_argument("--png", type=Path); p.add_argument("--width", type=int, default=DEFAULT_CLUT_WIDTH)
    p = sub.add_parser("verify"); p.add_argument("input", type=Path)
    args = ap.parse_args()
    if args.command == "unpack": unpack(args.input, args.output)
    elif args.command == "png": export_png(args.input, args.output)
    elif args.command == "compact": compact(args.input, args.output, args.png, args.width)
    else: verify(args.input)


if __name__ == "__main__":
    main()

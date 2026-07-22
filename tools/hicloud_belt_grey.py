#!/usr/bin/env python3
"""Apply the neutral RGB-140 belt refinement to verified Palette64 HiCloud."""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

from hicloud_clut_patch import lzs_compress, lzs_decompress
from hicloud_palette_compact import TIM_OFFSET, parse_tim, psx_rgba


EXPECTED_PALETTE64_SHA256 = (
    "9d073ca26d4312ddf1d7378dec48a476458805fa20b4a00b850df8d960ccf7bd"
)
EXPECTED_FILE_SIZE = 99118
EXPECTED_OUTPUT_SHA256 = (
    "72b2121727159403eb4e36bc7e8ff670f1230a6c84a90cb5060b1efbb6967746"
)
BELT_RECT = (12, 141, 52, 184)  # left, top, right-exclusive, bottom-exclusive

# Palette indices used exclusively by the belt in the verified Palette64 build.
# Level 17 displays as RGB 139,139,139, the nearest PS1 value to RGB 140.
GREY_RAMP = (
    (42, 2),
    (4, 3),
    (18, 5),
    (40, 6),
    (44, 8),
    (39, 10),
    (36, 13),
    (38, 15),
    (34, 17),
)
STP_BLACK_INDEX = 63


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rgb5_word(level: int) -> int:
    return level | (level << 5) | (level << 10)


def luminance(word: int) -> float:
    r, g, b, _ = psx_rgba(word)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def make_variant(source: Path) -> tuple[bytes, bytes, int]:
    wrapped = source.read_bytes()
    if sha256(wrapped) != EXPECTED_PALETTE64_SHA256:
        raise SystemExit(f"error: unexpected Palette64 source SHA-256: {sha256(wrapped)}")

    original = lzs_decompress(wrapped)
    dec = bytearray(original)
    palette, pixels_off, pixels = parse_tim(dec)
    if struct.unpack_from("<H", dec, TIM_OFFSET + 16)[0] != 64:
        raise SystemExit("error: source is not the 64-entry CLUT build")

    left, top, right, bottom = BELT_RECT
    belt_indices = {index for index, _ in GREY_RAMP} | {STP_BLACK_INDEX}
    outside_uses = [
        (pos % 128, pos // 128, index)
        for pos, index in enumerate(pixels)
        if index in belt_indices
        and not (left <= pos % 128 < right and top <= pos // 128 < bottom)
    ]
    if outside_uses:
        raise SystemExit(
            "error: belt palette indices are referenced outside the verified belt region"
        )

    for index, level in GREY_RAMP:
        word = rgb5_word(level)
        palette[index] = word
        struct.pack_into("<H", dec, TIM_OFFSET + 20 + index * 2, word)

    changed_pixels = 0
    for y in range(top, bottom):
        for x in range(left, right):
            pos = y * 128 + x
            source_index = pixels[pos]
            source_word = palette[source_index]
            if source_word == 0:
                continue
            if source_word & 0x8000:
                target_index = STP_BLACK_INDEX
            else:
                target_luma = luminance(source_word) * 0.80
                target_index, _ = min(
                    GREY_RAMP,
                    key=lambda pair: abs((pair[1] * 255 / 31) - target_luma),
                )
            if target_index != source_index:
                dec[pixels_off + pos] = target_index
                changed_pixels += 1

    packed = lzs_compress(bytes(dec))
    if len(packed) > EXPECTED_FILE_SIZE:
        raise SystemExit(f"error: compressed file grew to {len(packed)} bytes")
    disc_ready = packed + bytes(EXPECTED_FILE_SIZE - len(packed))
    if lzs_decompress(disc_ready) != bytes(dec):
        raise SystemExit("error: LZS round-trip validation failed")
    if sha256(disc_ready) != EXPECTED_OUTPUT_SHA256:
        raise SystemExit("error: output does not match the verified gray-belt target")
    return disc_ready, packed, changed_pixels


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="verified Palette64 HICLOUD.LZS")
    ap.add_argument("output", type=Path, help="disc-ready refined HICLOUD.LZS")
    ap.add_argument("--noesis", type=Path, help="optional unpadded inspection copy")
    args = ap.parse_args()

    disc_ready, trimmed, changed_pixels = make_variant(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(disc_ready)
    if args.noesis:
        args.noesis.parent.mkdir(parents=True, exist_ok=True)
        args.noesis.write_bytes(trimmed)

    print(f"belt texels remapped: {changed_pixels}")
    print(f"disc-ready: {args.output} ({len(disc_ready)} bytes, {sha256(disc_ready)})")
    if args.noesis:
        print(f"Noesis: {args.noesis} ({len(trimmed)} bytes, {sha256(trimmed)})")


if __name__ == "__main__":
    main()

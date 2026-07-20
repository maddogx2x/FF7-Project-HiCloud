#!/usr/bin/env python3
"""Relocate HICLOUD.LZS's 256-color CLUT into the framebuffer gap."""

from __future__ import annotations

import argparse
import hashlib
import struct
from collections import defaultdict, deque
from pathlib import Path


EXPECTED_INPUT_SHA256 = "3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4"
EXPECTED_DECOMPRESSED_SIZE = 161768
TIM_OFFSET = 92148
OLD_RECT = (320, 192, 256, 1)
# Keep all three loader-adjusted rows (base + physical_slot * 3) in the last
# eight lines between the two 256-line VRAM pages.  The earlier y=240 probe
# could become visible during display-page transitions; y=248 avoids that
# shallow edge while retaining a contiguous 256-word palette row.
NEW_RECT = (0, 248, 256, 1)


def lzs_decompress(wrapped: bytes) -> bytes:
    size = struct.unpack_from("<I", wrapped, 0)[0]
    src = wrapped[4 : 4 + size]
    out = bytearray()
    pos = 0
    while pos < len(src):
        flags = src[pos]
        pos += 1
        for bit in range(8):
            if pos >= len(src):
                break
            if flags & (1 << bit):
                out.append(src[pos])
                pos += 1
            else:
                lo, hi = src[pos], src[pos + 1]
                pos += 2
                offset = lo | ((hi & 0xF0) << 4)
                length = (hi & 0x0F) + 3
                ref = len(out) - ((len(out) - 18 - offset) & 0xFFF)
                for _ in range(length):
                    out.append(out[ref] if ref >= 0 else 0)
                    ref += 1
    return bytes(out)


def lzs_compress(data: bytes) -> bytes:
    # Greedy 4 KiB-window encoder for FF7's LZS variant. Candidate queues keep
    # the implementation fast while exhaustive comparison selects the longest
    # legal 3..18-byte match.
    positions: dict[bytes, deque[int]] = defaultdict(deque)
    encoded = bytearray()
    cursor = 0
    while cursor < len(data):
        control_pos = len(encoded)
        encoded.append(0)
        control = 0
        for bit in range(8):
            if cursor >= len(data):
                break
            best_pos = -1
            best_len = 0
            if cursor + 3 <= len(data):
                key = data[cursor : cursor + 3]
                candidates = positions[key]
                while candidates and candidates[0] < cursor - 4096:
                    candidates.popleft()
                for candidate in reversed(candidates):
                    limit = min(18, len(data) - cursor)
                    length = 3
                    while length < limit and data[candidate + length] == data[cursor + length]:
                        length += 1
                    if length > best_len:
                        best_pos, best_len = candidate, length
                        if length == limit:
                            break
            if best_len >= 3:
                offset = (best_pos - 18) & 0xFFF
                encoded.extend((offset & 0xFF, ((offset >> 4) & 0xF0) | (best_len - 3)))
                consumed = best_len
            else:
                control |= 1 << bit
                encoded.append(data[cursor])
                consumed = 1
            for index in range(cursor, cursor + consumed):
                if index + 3 <= len(data):
                    q = positions[data[index : index + 3]]
                    q.append(index)
                    while q and q[0] < index - 4096:
                        q.popleft()
            cursor += consumed
        encoded[control_pos] = control
    return struct.pack("<I", len(encoded)) + encoded


def build(source: Path, output: Path) -> None:
    wrapped = source.read_bytes()
    actual = hashlib.sha256(wrapped).hexdigest()
    if actual != EXPECTED_INPUT_SHA256:
        raise SystemExit(f"error: unexpected HICLOUD.LZS SHA-256: {actual}")
    dec = bytearray(lzs_decompress(wrapped))
    if len(dec) != EXPECTED_DECOMPRESSED_SIZE:
        raise SystemExit("error: unexpected decompressed HICLOUD size")
    if struct.unpack_from("<I", dec, TIM_OFFSET)[0] != 0x10:
        raise SystemExit("error: expected TIM record was not found")
    old = struct.unpack_from("<4H", dec, TIM_OFFSET + 12)
    if old != OLD_RECT:
        raise SystemExit(f"error: unexpected original CLUT rectangle: {old}")
    struct.pack_into("<4H", dec, TIM_OFFSET + 12, *NEW_RECT)
    result = lzs_compress(bytes(dec))
    if len(result) > len(wrapped):
        raise SystemExit(
            f"error: recompressed archive grew from {len(wrapped)} to {len(result)} bytes"
        )
    result += bytes(len(wrapped) - len(result))
    check = lzs_decompress(result)
    if check != bytes(dec):
        raise SystemExit("error: LZS round-trip validation failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    print(f"wrote: {output}")
    print(f"CLUT: {OLD_RECT[:2]} -> {NEW_RECT[:2]}")
    print(f"size: {len(result)} bytes")
    print(f"sha256: {hashlib.sha256(result).hexdigest()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.input, args.output)


if __name__ == "__main__":
    main()

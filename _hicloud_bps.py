#!/usr/bin/env python3
"""Minimal standards-compliant BPS1 creator/applier for release packaging."""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


def encode_number(value: int) -> bytes:
    if value < 0:
        raise ValueError("BPS numbers cannot be negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value == 0:
            out.append(byte | 0x80)
            return bytes(out)
        out.append(byte)
        value -= 1


def decode_number(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 1
    while True:
        if pos >= len(data):
            raise ValueError("truncated BPS variable-length number")
        byte = data[pos]
        pos += 1
        value += (byte & 0x7F) * shift
        if byte & 0x80:
            return value, pos
        shift <<= 7
        value += shift


def create(source: bytes, target: bytes, metadata: bytes = b"") -> bytes:
    """Create a simple BPS patch using SourceRead and TargetRead actions."""
    patch = bytearray(b"BPS1")
    patch += encode_number(len(source))
    patch += encode_number(len(target))
    patch += encode_number(len(metadata))
    patch += metadata

    pos = 0
    while pos < len(target):
        equal = pos < len(source) and source[pos] == target[pos]
        end = pos + 1
        if equal:
            while end < len(target) and end < len(source) and source[end] == target[end]:
                end += 1
            patch += encode_number(((end - pos - 1) << 2) | 0)
        else:
            while end < len(target) and not (
                end < len(source) and source[end] == target[end]
            ):
                end += 1
            patch += encode_number(((end - pos - 1) << 2) | 1)
            patch += target[pos:end]
        pos = end

    patch += struct.pack("<I", zlib.crc32(source) & 0xFFFFFFFF)
    patch += struct.pack("<I", zlib.crc32(target) & 0xFFFFFFFF)
    patch += struct.pack("<I", zlib.crc32(patch) & 0xFFFFFFFF)
    return bytes(patch)


def apply(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 19 or patch[:4] != b"BPS1":
        raise ValueError("not a BPS1 patch")
    expected_patch_crc = struct.unpack_from("<I", patch, len(patch) - 4)[0]
    if zlib.crc32(patch[:-4]) & 0xFFFFFFFF != expected_patch_crc:
        raise ValueError("BPS patch checksum mismatch")

    pos = 4
    source_size, pos = decode_number(patch, pos)
    target_size, pos = decode_number(patch, pos)
    metadata_size, pos = decode_number(patch, pos)
    pos += metadata_size
    if source_size != len(source):
        raise ValueError("BPS source size mismatch")
    expected_source_crc = struct.unpack_from("<I", patch, len(patch) - 12)[0]
    if zlib.crc32(source) & 0xFFFFFFFF != expected_source_crc:
        raise ValueError("BPS source checksum mismatch")

    target = bytearray()
    source_relative = 0
    target_relative = 0
    action_end = len(patch) - 12
    while len(target) < target_size:
        if pos >= action_end:
            raise ValueError("truncated BPS action stream")
        action, pos = decode_number(patch, pos)
        length = (action >> 2) + 1
        mode = action & 3
        if mode == 0:  # SourceRead at current output offset
            start = len(target)
            target += source[start : start + length]
        elif mode == 1:  # TargetRead literals
            if pos + length > action_end:
                raise ValueError("truncated BPS literal data")
            target += patch[pos : pos + length]
            pos += length
        elif mode == 2:  # SourceCopy
            encoded, pos = decode_number(patch, pos)
            source_relative += -(encoded >> 1) if encoded & 1 else encoded >> 1
            target += source[source_relative : source_relative + length]
            source_relative += length
        else:  # TargetCopy
            encoded, pos = decode_number(patch, pos)
            target_relative += -(encoded >> 1) if encoded & 1 else encoded >> 1
            for _ in range(length):
                target.append(target[target_relative])
                target_relative += 1

    if len(target) != target_size:
        raise ValueError("BPS target size mismatch")
    expected_target_crc = struct.unpack_from("<I", patch, len(patch) - 8)[0]
    if zlib.crc32(target) & 0xFFFFFFFF != expected_target_crc:
        raise ValueError("BPS target checksum mismatch")
    return bytes(target)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    create_ap = sub.add_parser("create")
    create_ap.add_argument("source", type=Path)
    create_ap.add_argument("target", type=Path)
    create_ap.add_argument("patch", type=Path)
    create_ap.add_argument("--metadata", default="")
    apply_ap = sub.add_parser("apply")
    apply_ap.add_argument("source", type=Path)
    apply_ap.add_argument("patch", type=Path)
    apply_ap.add_argument("target", type=Path)
    args = ap.parse_args()
    if args.command == "create":
        result = create(
            args.source.read_bytes(), args.target.read_bytes(), args.metadata.encode("utf-8")
        )
        args.patch.write_bytes(result)
    else:
        result = apply(args.source.read_bytes(), args.patch.read_bytes())
        args.target.write_bytes(result)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build a verified Project HiCloud 8 MiB/Palette64 NTSC-U BIN/CUE."""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from _hicloud_bps import apply as apply_bps
from _hicloud_iso import (
    HICLOUD_LBA,
    HICLOUD_SIZE,
    ORIGINAL_BATTLE_SHA256,
    ORIGINAL_HICLOUD_SHA256,
    identify_disc,
    inject,
    locate_iso_files,
    parse_cue,
    sha256,
    write_cue,
)


RELEASE = "Project HiCloud 8 MiB/Palette64 v1.0.0-rc2"
BATTLE_PATCH = "HiCloud_8MB_BATTLE.X.bps.b64"
HICLOUD_PATCH = "HiCloud_Palette64_HICLOUD.LZS.bps.b64"
PATCHED_BATTLE_SHA256 = "00c85f473325ba3883169e386fb82ad9ccb02519c35662e70b82b11379e41757"
PATCHED_HICLOUD_SHA256 = "72b2121727159403eb4e36bc7e8ff670f1230a6c84a90cb5060b1efbb6967746"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"error: {message}")


def apply_checked(source: bytes, name: str, expected: str) -> bytes:
    path = Path(__file__).resolve().parent / "patches" / name
    if not path.is_file():
        fail(f"release patch is missing: {path}")
    try:
        result = apply_bps(source, base64.b64decode(path.read_text(encoding="ascii")))
    except ValueError as exc:
        fail(f"{name} validation failed: {exc}")
    if sha256(result) != expected:
        fail(f"{name} produced an unexpected target hash")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cue", type=Path, help="original FF7 NTSC-U single-BIN CUE")
    ap.add_argument("-o", "--output-stem", type=Path,
                    help="output path without extension")
    args = ap.parse_args()

    cue = args.cue.expanduser().resolve()
    if not cue.is_file():
        fail(f"CUE file not found: {cue}")
    source_bin, track_frame, cue_text = parse_cue(cue)
    (battle_lba, battle_size), battle, hicloud_allocation, system = locate_iso_files(
        source_bin, track_frame
    )
    serial = identify_disc(system)
    original_hicloud = hicloud_allocation[:HICLOUD_SIZE]
    if sha256(battle) != ORIGINAL_BATTLE_SHA256:
        fail("disc BATTLE.X is modified or is not the verified NTSC-U file")
    if sha256(original_hicloud) != ORIGINAL_HICLOUD_SHA256:
        fail("native HICLOUD.LZS is modified or is not the verified NTSC-U file")

    patched_battle = apply_checked(battle, BATTLE_PATCH, PATCHED_BATTLE_SHA256)
    patched_hicloud = apply_checked(
        original_hicloud, HICLOUD_PATCH, PATCHED_HICLOUD_SHA256
    )
    if len(patched_battle) != battle_size or len(patched_hicloud) != HICLOUD_SIZE:
        fail("patched resource size changed unexpectedly")

    stem = (args.output_stem.expanduser().resolve() if args.output_stem else
            cue.with_name(f"{cue.stem}_HiCloud_8MB_Palette64").with_suffix(""))
    output_bin = Path(f"{stem}.bin")
    output_cue = Path(f"{stem}.cue")
    if output_bin == source_bin:
        fail("output BIN must not be the source BIN")
    if output_bin.exists() or output_cue.exists():
        fail("refusing to overwrite an existing output BIN/CUE")
    stem.parent.mkdir(parents=True, exist_ok=True)

    print(RELEASE)
    print(f"disc: {serial}")
    print("copying source image and rebuilding patched raw sectors...")
    try:
        inject(source_bin, output_bin, track_frame, battle_lba,
               patched_battle, "BATTLE.X", initialize=True)
        inject(source_bin, output_bin, track_frame, HICLOUD_LBA,
               patched_hicloud, "HICLOUD.LZS")
        write_cue(cue_text, source_bin, output_bin, output_cue)

        (_, verify_size), verify_battle, verify_hicloud, verify_system = locate_iso_files(
            output_bin, track_frame
        )
        if verify_size != len(patched_battle) or verify_battle != patched_battle:
            fail("post-write BATTLE.X verification failed")
        if verify_hicloud[:HICLOUD_SIZE] != patched_hicloud:
            fail("post-write HICLOUD.LZS verification failed")
        if identify_disc(verify_system) != serial:
            fail("disc identity changed unexpectedly")
    except BaseException:
        output_bin.unlink(missing_ok=True)
        output_cue.unlink(missing_ok=True)
        raise

    print("SUCCESS - verified output created")
    print(f"BIN: {output_bin}")
    print(f"CUE: {output_cue}")
    print("Enable 8 MiB PSX RAM before booting the new CUE.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nCancelled; the original image was not modified.")

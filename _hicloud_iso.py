#!/usr/bin/env python3
"""Inject a same-size Project HiCloud BATTLE.X into an FF7 US BIN/CUE image.

The script validates the ISO9660 file, disc serial, raw-sector integrity, and
replacement hashes before writing a new BIN/CUE pair. It never edits the source
image in place.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import struct
from pathlib import Path


SECTOR = 2352
USER_OFFSET = 24
USER_SIZE = 2048
ORIGINAL_BATTLE_SHA256 = (
    "0ac8e4297dd83226e18ebeffc20d1c372fd8ca7838c1edb7ec48138858f08e88"
)
PATCHED_BATTLE_SHA256S = {
    "d4c020c05ecf261548c464b968ccac3842080938c237e821ee51ed69f20adbe7",
    "00c85f473325ba3883169e386fb82ad9ccb02519c35662e70b82b11379e41757",
    "04fe3fb823117f89f998bea5240b94e8bdabffdd2286e8664d0d11410bdf4f79",
    "25c52536b0be95c5fece50fe7799d34ad8c705e7b81f9b56692dbf6a8c7ca417",
}
ORIGINAL_HICLOUD_SHA256 = (
    "3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4"
)
PATCHED_HICLOUD_SHA256S = {
    "2a53d2bddad719e74547def5e1c0446847667a3624b3aa9b427260cbd4645fe0",
    "87215c01cac3c221773864b8f42e123c7d38fe951c8755449269628134e65f88",
    "415167ce2d43b4abf3d8e1894945507e870e0eb189be04c757c3dcdce2f2cabe",
    "7a3e6979a4a39133e78dce4c51bec2398cc0862003170be5e979b64dd9bd890c",
    "318e4817b53d35285539d2746bd6eb66aebee374e617f5b8870d4cda67885191",
    "19ea0dc73fc6ec9c015cd4cd20769ff2c6094718531e59ea1514f4d40f2890bc",
    "765062448c287c6dbf5cf9d2860e04e55b6997febf44cf984dbe6faff0173a4a",
    "05b1315dfe40ff5bb6921e53e5ebb7e0f24d61b9604bd1011a2f95540af7e3e1",
    "24abf655f774575c7bbd02200177ef4ecf48863214c4768045c9a01330f2ad9a",
    "9d073ca26d4312ddf1d7378dec48a476458805fa20b4a00b850df8d960ccf7bd",
}
SUPPORTED_SERIALS = {"SCUS_941.63", "SCUS_941.64", "SCUS_941.65"}
HICLOUD_LBA = 0x77B5
HICLOUD_SIZE = 99118
HICLOUD_ALLOCATION = 0x18800


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def frames(msf: str) -> int:
    m, s, f = (int(x) for x in msf.split(":"))
    return (m * 60 + s) * 75 + f


def parse_cue(cue: Path) -> tuple[Path, int, str]:
    text = cue.read_text(encoding="utf-8-sig", errors="strict")
    files = re.findall(r'^\s*FILE\s+"([^"]+)"\s+BINARY\s*$', text, re.I | re.M)
    if len(files) != 1:
        raise SystemExit("error: this prototype requires a single-BIN CUE sheet")
    match = re.search(
        r"^\s*TRACK\s+\d+\s+MODE2/2352\s*$.*?^\s*INDEX\s+01\s+(\d\d:\d\d:\d\d)\s*$",
        text,
        re.I | re.M | re.S,
    )
    if not match:
        raise SystemExit("error: no MODE2/2352 data track with INDEX 01 was found")
    bin_path = (cue.parent / files[0]).resolve()
    if not bin_path.is_file():
        raise SystemExit(f"error: BIN referenced by CUE does not exist: {bin_path}")
    return bin_path, frames(match.group(1)), text


def sector_offset(track_frame: int, lba: int) -> int:
    return (track_frame + lba) * SECTOR


def check_form1_sector(raw: bytes, label: str) -> None:
    if len(raw) != SECTOR:
        raise SystemExit(f"error: truncated raw sector while reading {label}")
    if raw[:12] != b"\x00" + b"\xff" * 10 + b"\x00" or raw[15] != 2:
        raise SystemExit(f"error: {label} is not a raw Mode 2 sector")
    if raw[16:20] != raw[20:24] or (raw[18] & 0x20):
        raise SystemExit(f"error: {label} is not a Mode 2 Form 1 sector")


def read_user_sector(f, track_frame: int, lba: int) -> bytes:
    f.seek(sector_offset(track_frame, lba))
    raw = f.read(SECTOR)
    check_form1_sector(raw, f"LBA {lba}")
    return raw[USER_OFFSET : USER_OFFSET + USER_SIZE]


def read_extent(f, track_frame: int, lba: int, size: int) -> bytes:
    out = bytearray()
    for i in range((size + USER_SIZE - 1) // USER_SIZE):
        out.extend(read_user_sector(f, track_frame, lba + i))
    return bytes(out[:size])


def dir_record(record: bytes) -> tuple[int, int, bool, str]:
    extent = struct.unpack_from("<I", record, 2)[0]
    size = struct.unpack_from("<I", record, 10)[0]
    is_dir = bool(record[25] & 2)
    name_len = record[32]
    raw_name = record[33 : 33 + name_len]
    if raw_name == b"\x00":
        name = "."
    elif raw_name == b"\x01":
        name = ".."
    else:
        name = raw_name.decode("ascii", errors="strict").split(";", 1)[0].upper()
    return extent, size, is_dir, name


def find_child(f, track_frame: int, directory: tuple[int, int], wanted: str):
    lba, size = directory
    data = read_extent(f, track_frame, lba, size)
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0:
            pos = ((pos // USER_SIZE) + 1) * USER_SIZE
            continue
        record = data[pos : pos + length]
        if len(record) != length:
            break
        extent, child_size, is_dir, name = dir_record(record)
        if name == wanted.upper():
            return extent, child_size, is_dir
        pos += length
    raise SystemExit(f"error: ISO9660 entry not found: {wanted}")


def locate_iso_files(bin_path: Path, track_frame: int):
    with bin_path.open("rb") as f:
        pvd = read_user_sector(f, track_frame, 16)
        if pvd[0] != 1 or pvd[1:6] != b"CD001" or pvd[6] != 1:
            raise SystemExit("error: ISO9660 primary volume descriptor not found")
        root_record_len = pvd[156]
        root = dir_record(pvd[156 : 156 + root_record_len])
        root_dir = (root[0], root[1])
        battle_dir = find_child(f, track_frame, root_dir, "BATTLE")
        if not battle_dir[2]:
            raise SystemExit("error: /BATTLE is not an ISO directory")
        battle_x = find_child(f, track_frame, battle_dir[:2], "BATTLE.X")
        system_cnf = find_child(f, track_frame, root_dir, "SYSTEM.CNF")
        if battle_x[2] or system_cnf[2]:
            raise SystemExit("error: unexpected ISO directory entry type")
        battle_data = read_extent(f, track_frame, battle_x[0], battle_x[1])
        # Character archives in the YAMADA table are addressed by raw LBA and
        # are not represented as individual ISO9660 directory entries.
        hicloud_data = read_extent(f, track_frame, HICLOUD_LBA, HICLOUD_ALLOCATION)
        system_data = read_extent(f, track_frame, system_cnf[0], system_cnf[1])
    return battle_x[:2], battle_data, hicloud_data, system_data


def identify_disc(system_cnf: bytes) -> str:
    text = system_cnf.decode("ascii", errors="ignore").upper()
    match = re.search(r"SCUS[_-]941[.]6[345]", text)
    if not match or match.group(0) not in SUPPORTED_SERIALS:
        raise SystemExit("error: BIN is not one of the supported US FF7 discs")
    return match.group(0)


def make_tables():
    edc = [0] * 256
    f_lut = [0] * 256
    b_lut = [0] * 256
    for i in range(256):
        value = i
        for _ in range(8):
            value = (value >> 1) ^ (0xD8018001 if value & 1 else 0)
        edc[i] = value & 0xFFFFFFFF
        j = i << 1
        if j & 0x100:
            j ^= 0x11D
        f_lut[i] = j
        b_lut[i ^ j] = i
    return edc, f_lut, b_lut


EDC_LUT, ECC_F_LUT, ECC_B_LUT = make_tables()


def edc_compute(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ EDC_LUT[(value ^ byte) & 0xFF]
    return value & 0xFFFFFFFF


def ecc_compute(source: bytes, major_count: int, minor_count: int,
                major_mult: int, minor_inc: int) -> bytes:
    size = major_count * minor_count
    dest = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            temp = source[index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ECC_F_LUT[ecc_a]
        ecc_a = ECC_B_LUT[ECC_F_LUT[ecc_a] ^ ecc_b]
        dest[major] = ecc_a
        dest[major + major_count] = ecc_a ^ ecc_b
    return bytes(dest)


def rebuild_mode2_form1(raw: bytearray) -> None:
    check_form1_sector(raw, "sector being rebuilt")
    struct.pack_into("<I", raw, 2072, edc_compute(raw[16:2072]))
    address = bytes(raw[12:16])
    raw[12:16] = b"\x00" * 4
    raw[2076:2248] = ecc_compute(bytes(raw[12:2076]), 86, 24, 2, 86)
    raw[2248:2352] = ecc_compute(bytes(raw[12:2248]), 52, 43, 86, 88)
    raw[12:16] = address


def verify_raw_integrity(raw: bytes) -> None:
    rebuilt = bytearray(raw)
    rebuild_mode2_form1(rebuilt)
    if rebuilt[2072:] != raw[2072:]:
        raise SystemExit("error: source BIN sector EDC/ECC validation failed")


def inject(source_bin: Path, output_bin: Path, track_frame: int,
           extent_lba: int, replacement: bytes, label: str,
           initialize: bool = False) -> None:
    if initialize:
        shutil.copyfile(source_bin, output_bin)
    with output_bin.open("r+b") as f:
        for i in range((len(replacement) + USER_SIZE - 1) // USER_SIZE):
            offset = sector_offset(track_frame, extent_lba + i)
            f.seek(offset)
            raw = bytearray(f.read(SECTOR))
            check_form1_sector(raw, f"{label} sector {i}")
            verify_raw_integrity(bytes(raw))
            chunk = replacement[i * USER_SIZE : (i + 1) * USER_SIZE]
            raw[USER_OFFSET : USER_OFFSET + len(chunk)] = chunk
            rebuild_mode2_form1(raw)
            f.seek(offset)
            f.write(raw)


def write_cue(source_text: str, source_bin: Path, output_bin: Path, output_cue: Path):
    pattern = re.compile(r'^(\s*FILE\s+")[^"]+("\s+BINARY\s*)$', re.I | re.M)
    rewritten, count = pattern.subn(rf"\g<1>{output_bin.name}\g<2>", source_text, count=1)
    if count != 1:
        raise SystemExit("error: failed to rewrite output CUE filename")
    output_cue.write_text(rewritten, encoding="utf-8", newline="")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cue", type=Path, help="original FF7 US .cue")
    ap.add_argument("battle_x", type=Path, help="BATTLE_HICLOUD_POSITIONAL_V4.X")
    ap.add_argument(
        "--hicloud-lzs",
        type=Path,
        help="optional verified experimental HICLOUD.LZS replacement",
    )
    ap.add_argument("-o", "--output-stem", type=Path, required=True,
                    help="output path without .bin/.cue extension")
    args = ap.parse_args()

    source_bin, track_frame, cue_text = parse_cue(args.cue.resolve())
    replacement = args.battle_x.read_bytes()
    if sha256(replacement) not in PATCHED_BATTLE_SHA256S:
        raise SystemExit("error: replacement is not the verified HiCloud prototype")

    hicloud_replacement = args.hicloud_lzs.read_bytes() if args.hicloud_lzs else None
    if hicloud_replacement is not None and sha256(hicloud_replacement) not in PATCHED_HICLOUD_SHA256S:
        raise SystemExit("error: HICLOUD.LZS is not a verified experimental replacement")

    (extent_lba, file_size), original, hicloud_original, system_cnf = locate_iso_files(source_bin, track_frame)
    serial = identify_disc(system_cnf)
    if file_size != len(replacement):
        raise SystemExit(
            f"error: ISO BATTLE.X is {file_size} bytes but replacement is "
            f"{len(replacement)} bytes"
        )
    if sha256(original) != ORIGINAL_BATTLE_SHA256:
        raise SystemExit("error: ISO BATTLE.X does not match the verified US file")
    if hicloud_replacement is not None:
        if not (HICLOUD_SIZE <= len(hicloud_replacement) <= HICLOUD_ALLOCATION):
            raise SystemExit(
                f"error: HICLOUD.LZS must fit its {HICLOUD_ALLOCATION}-byte allocation; "
                f"replacement is {len(hicloud_replacement)} bytes"
            )
        if sha256(hicloud_original[:HICLOUD_SIZE]) != ORIGINAL_HICLOUD_SHA256:
            raise SystemExit("error: ISO HICLOUD.LZS does not match the verified US file")

    stem = args.output_stem.resolve()
    stem.parent.mkdir(parents=True, exist_ok=True)
    output_bin = stem.with_suffix(".bin")
    output_cue = stem.with_suffix(".cue")
    if output_bin == source_bin:
        raise SystemExit("error: output BIN must not be the source BIN")

    inject(source_bin, output_bin, track_frame, extent_lba, replacement, "BATTLE.X", initialize=True)
    if hicloud_replacement is not None:
        inject(source_bin, output_bin, track_frame, HICLOUD_LBA,
               hicloud_replacement, "HICLOUD.LZS")
    write_cue(cue_text, source_bin, output_bin, output_cue)

    # Read back through ISO9660 and verify the patched file byte-for-byte.
    (_, verify_size), verify_data, verify_hicloud, verify_system = locate_iso_files(output_bin, track_frame)
    if verify_size != len(replacement) or verify_data != replacement:
        raise SystemExit("error: post-write ISO verification failed")
    if identify_disc(verify_system) != serial:
        raise SystemExit("error: disc identity changed unexpectedly")
    if hicloud_replacement is not None and (
        verify_hicloud[:len(hicloud_replacement)] != hicloud_replacement
    ):
        raise SystemExit("error: post-write HICLOUD.LZS verification failed")

    print(f"disc: {serial}")
    print(f"BATTLE.X extent LBA: {extent_lba}")
    print(f"wrote: {output_bin}")
    print(f"wrote: {output_cue}")
    print(f"BATTLE.X sha256: {sha256(verify_data)}")
    if hicloud_replacement is not None:
        print(f"HICLOUD.LZS raw LBA: {HICLOUD_LBA}")
        print(f"HICLOUD.LZS sha256: {sha256(verify_hicloud)}")


if __name__ == "__main__":
    main()

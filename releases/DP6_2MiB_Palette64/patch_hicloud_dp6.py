#!/usr/bin/env python3
"""Build the experimental FFVII NTSC-U HiCloud DP6 disc image."""

from __future__ import annotations

import gzip
import hashlib
import base64
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import BinaryIO

from _hicloud_bps import apply as apply_bps


VERSION = "DP6-MDEC-Raw-Restore-Palette64"
LOAD_ADDRESS = 0x800A0000
ACTOR_ARENA_BASE = 0x80103200
ACTOR_ARENA_LIMIT = 0x80151200
TEMP_MODEL_BASE = 0x80052800
TEMP_MODEL_LIMIT = 0x80062000
TEMP_MODEL_CAPACITY = TEMP_MODEL_LIMIT - TEMP_MODEL_BASE
MAIN_RAM_LOAD_ADDRESS = 0x80010000
PSX_EXE_HEADER_SIZE = 0x800
MAIN_RESTORE_FILE_OFFSET = (
    PSX_EXE_HEADER_SIZE + TEMP_MODEL_BASE - MAIN_RAM_LOAD_ADDRESS
)
MAIN_RESTORE_SECTOR_OFFSET = MAIN_RESTORE_FILE_OFFSET // 2048
MAIN_RESTORE_SIZE = TEMP_MODEL_CAPACITY
MAIN_EXECUTABLE_NAMES = ("SCUS_941.63", "SCUS_941.64", "SCUS_941.65")
DISC_NUMBER_BY_EXECUTABLE = {
    "SCUS_941.63": 1,
    "SCUS_941.64": 2,
    "SCUS_941.65": 3,
}
EXPECTED_MAIN_RESTORE_WINDOW_SHA256 = (
    "c3b1d2b3a49b928a176210713ac8334e5a6b45eec2c774305dbc0e9084fa0a44"
)
HICLOUD_LBA = 0x77B5
HICLOUD_SIZE = 99_118
ORIGINAL_HICLOUD_SHA256 = (
    "3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4"
)
PATCHED_HICLOUD_SHA256 = (
    "72b2121727159403eb4e36bc7e8ff670f1230a6c84a90cb5060b1efbb6967746"
)
PALETTE_PATCH_NAME = "HiCloud_Palette64_HICLOUD.LZS.bps.b64"
REFERENCE_PACKED_SHA256 = (
    "0ac8e4297dd83226e18ebeffc20d1c372fd8ca7838c1edb7ec48138858f08e88"
)
# Kept as an alias for older tests/scripts that imported this constant.
EXPECTED_INPUT_SHA256 = REFERENCE_PACKED_SHA256
EXPECTED_DECOMPRESSED_SHA1 = "c82690f814b18664c3b2d024f4edeea5743995d9"

# DP6 injects the clean disc's executable-sector LBA into BATTLE.X. The
# resulting BATTLE.X digest is therefore intentionally calculated per disc
# instead of being a single package-wide constant.


class DiscPatchError(RuntimeError):
    pass


def u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


@dataclass(frozen=True)
class CueTrack:
    file_path: Path
    filename_text: str
    mode: str
    index01_frames: int


@dataclass(frozen=True)
class IsoFile:
    path: str
    lba: int
    size: int


def cue_time_to_frames(value: str) -> int:
    try:
        mm, ss, ff = (int(part) for part in value.split(":"))
    except Exception as exc:
        raise DiscPatchError(f"Invalid CUE time: {value}") from exc
    return (mm * 60 + ss) * 75 + ff


def parse_single_bin_mode2_cue(cue_path: Path) -> tuple[CueTrack, str]:
    text = cue_path.read_text(encoding="utf-8-sig", errors="replace")
    file_re = re.compile(r'^(\s*FILE\s+)(?:"([^"]+)"|(\S+))(\s+\S+.*)$', re.I)
    track_re = re.compile(r"^\s*TRACK\s+\d+\s+(\S+)", re.I)
    index_re = re.compile(r"^\s*INDEX\s+01\s+(\d+:\d+:\d+)", re.I)

    filenames: list[str] = []
    mode: str | None = None
    index01: int | None = None
    for line in text.splitlines():
        if match := file_re.match(line):
            filenames.append(match.group(2) or match.group(3))
        elif match := track_re.match(line):
            if mode is None:
                mode = match.group(1).upper()
        elif match := index_re.match(line):
            if index01 is None:
                index01 = cue_time_to_frames(match.group(1))

    unique_files = list(dict.fromkeys(filenames))
    if len(unique_files) != 1:
        raise DiscPatchError(
            "DP6 currently requires a standard single-BIN CUE. "
            f"Found {len(unique_files)} referenced files."
        )
    if mode != "MODE2/2352":
        raise DiscPatchError(
            f"DP6 requires a raw MODE2/2352 data track; found {mode or 'none'}."
        )
    if index01 is None:
        raise DiscPatchError("The CUE does not contain an INDEX 01 entry.")
    filename = unique_files[0]
    bin_path = (cue_path.parent / filename).resolve()
    if not bin_path.is_file():
        raise DiscPatchError(f"The BIN referenced by the CUE was not found: {bin_path}")
    return CueTrack(bin_path, filename, mode, index01), text


class Mode2SectorReader:
    def __init__(self, file_path: Path, index01_frames: int):
        self.file_path = file_path
        self.index01_frames = index01_frames
        self._file: BinaryIO | None = None

    def __enter__(self) -> "Mode2SectorReader":
        self._file = self.file_path.open("rb")
        return self

    def __exit__(self, *_: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def read_raw_sector(self, lba: int) -> bytes:
        if self._file is None:
            raise RuntimeError("Sector reader is not open.")
        offset = (self.index01_frames + lba) * 2352
        self._file.seek(offset)
        raw = self._file.read(2352)
        if len(raw) != 2352:
            raise DiscPatchError(f"Short raw-sector read at ISO LBA {lba}.")
        if raw[:12] != b"\x00" + b"\xFF" * 10 + b"\x00" or raw[15] != 2:
            raise DiscPatchError(f"ISO LBA {lba} is not a raw Mode 2 sector.")
        if raw[16:20] != raw[20:24] or raw[18] & 0x20:
            raise DiscPatchError(f"ISO LBA {lba} is not a Mode 2 Form 1 sector.")
        return raw

    def read_user_sector(self, lba: int) -> bytes:
        return self.read_raw_sector(lba)[24:2072]

    def read_extent(self, lba: int, byte_length: int) -> bytes:
        output = bytearray()
        for index in range((byte_length + 2047) // 2048):
            output.extend(self.read_user_sector(lba + index))
        return bytes(output[:byte_length])


class Iso9660:
    def __init__(self, sectors: Mode2SectorReader):
        self.sectors = sectors
        pvd = sectors.read_user_sector(16)
        if pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise DiscPatchError("No ISO9660 primary volume descriptor was found.")
        root_lba, root_size, is_dir, _ = self._decode_record(pvd, 156)
        if not is_dir:
            raise DiscPatchError("The ISO9660 root directory record is invalid.")
        self.files: dict[str, IsoFile] = {}
        self._walk(root_lba, root_size, PurePosixPath(""))

    @staticmethod
    def _decode_record(data: bytes, offset: int) -> tuple[int, int, bool, str]:
        length = data[offset]
        if length < 34 or offset + length > len(data):
            raise DiscPatchError("Malformed ISO9660 directory record.")
        lba = u32(data, offset + 2)
        size = u32(data, offset + 10)
        is_dir = bool(data[offset + 25] & 2)
        name_length = data[offset + 32]
        raw_name = data[offset + 33 : offset + 33 + name_length]
        if raw_name == b"\x00":
            name = "."
        elif raw_name == b"\x01":
            name = ".."
        else:
            name = raw_name.decode("ascii", errors="replace").split(";")[0]
        return lba, size, is_dir, name

    def _walk(
        self, lba: int, size: int, parent: PurePosixPath, depth: int = 0
    ) -> None:
        if depth > 12:
            raise DiscPatchError("ISO9660 directory nesting is unexpectedly deep.")
        data = self.sectors.read_extent(lba, size)
        offset = 0
        while offset < len(data):
            record_length = data[offset]
            if record_length == 0:
                offset = align(offset + 1, 2048)
                continue
            rec_lba, rec_size, is_dir, name = self._decode_record(data, offset)
            offset += record_length
            if name in (".", ".."):
                continue
            path = parent / name
            normalized = str(path).replace("\\", "/").upper()
            if is_dir:
                self._walk(rec_lba, rec_size, path, depth + 1)
            else:
                self.files[normalized] = IsoFile(normalized, rec_lba, rec_size)


def build_edc_ecc_tables() -> tuple[list[int], list[int], list[int]]:
    edc_lut: list[int] = []
    ecc_forward: list[int] = []
    ecc_backward = [0] * 256
    for value in range(256):
        edc = value
        for _ in range(8):
            edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
        edc_lut.append(edc)

        forward = value << 1
        if value & 0x80:
            forward ^= 0x11D
        ecc_forward.append(forward)
        ecc_backward[value ^ forward] = value
    return edc_lut, ecc_forward, ecc_backward


EDC_LUT, ECC_FORWARD, ECC_BACKWARD = build_edc_ecc_tables()


def compute_edc(data: bytes) -> int:
    edc = 0
    for value in data:
        edc = (edc >> 8) ^ EDC_LUT[(edc ^ value) & 0xFF]
    return edc


def compute_ecc(
    source: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    size = major_count * minor_count
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            value = source[index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= value
            ecc_b ^= value
            ecc_a = ECC_FORWARD[ecc_a]
        ecc_a = ECC_BACKWARD[ECC_FORWARD[ecc_a] ^ ecc_b]
        output[major] = ecc_a
        output[major + major_count] = ecc_a ^ ecc_b
    return bytes(output)


def regenerate_mode2_form1_checksums(sector: bytearray) -> None:
    if len(sector) != 2352:
        raise ValueError("Raw CD sector must be exactly 2352 bytes.")
    sector[2072:2076] = struct.pack("<I", compute_edc(bytes(sector[16:2072])))
    address = bytes(sector[12:16])
    sector[12:16] = b"\x00" * 4
    sector[2076:2248] = compute_ecc(bytes(sector[12:2076]), 86, 24, 2, 86)
    sector[2248:2352] = compute_ecc(bytes(sector[12:2248]), 52, 43, 86, 88)
    sector[12:16] = address


def write_iso_extent(
    bin_path: Path,
    index01_frames: int,
    entry: IsoFile,
    replacement: bytes,
) -> None:
    if len(replacement) != entry.size:
        raise DiscPatchError(
            "Replacement size differs from the ISO file size; refusing to change layout."
        )
    with bin_path.open("r+b") as image:
        source_offset = 0
        for sector_index in range((entry.size + 2047) // 2048):
            raw_offset = (index01_frames + entry.lba + sector_index) * 2352
            image.seek(raw_offset)
            sector = bytearray(image.read(2352))
            if len(sector) != 2352:
                raise DiscPatchError("Short sector read while patching the copied BIN.")
            if (
                sector[:12] != b"\x00" + b"\xFF" * 10 + b"\x00"
                or sector[15] != 2
                or sector[16:20] != sector[20:24]
                or sector[18] & 0x20
            ):
                raise DiscPatchError(
                    f"BATTLE.X sector at ISO LBA {entry.lba + sector_index} "
                    "is not Mode 2 Form 1."
                )
            count = min(2048, entry.size - source_offset)
            sector[24 : 24 + count] = replacement[source_offset : source_offset + count]
            regenerate_mode2_form1_checksums(sector)
            image.seek(raw_offset)
            image.write(sector)
            source_offset += count


def find_verified_main_executable(
    iso: Iso9660,
    sectors: Mode2SectorReader,
) -> tuple[IsoFile, int]:
    candidates = [
        entry
        for path, entry in iso.files.items()
        if path.rsplit("/", 1)[-1] in MAIN_EXECUTABLE_NAMES
    ]
    if len(candidates) != 1:
        raise DiscPatchError(
            "Expected exactly one NTSC-U FFVII boot executable "
            f"({', '.join(MAIN_EXECUTABLE_NAMES)}); found {len(candidates)}."
        )

    entry = candidates[0]
    required_size = MAIN_RESTORE_FILE_OFFSET + MAIN_RESTORE_SIZE
    if entry.size < required_size:
        raise DiscPatchError(
            f"{entry.path} is too small to contain the verified MDEC/VLC window."
        )
    prefix = sectors.read_extent(entry.lba, required_size)
    if prefix[:8] != b"PS-X EXE":
        raise DiscPatchError(f"{entry.path} does not have a PS-X EXE header.")
    if u32(prefix, 0x18) != MAIN_RAM_LOAD_ADDRESS:
        raise DiscPatchError(
            f"{entry.path} has an unexpected RAM load address "
            f"0x{u32(prefix, 0x18):08X}."
        )
    restore_window = prefix[
        MAIN_RESTORE_FILE_OFFSET : MAIN_RESTORE_FILE_OFFSET + MAIN_RESTORE_SIZE
    ]
    actual_window_hash = sha256(restore_window)
    if actual_window_hash != EXPECTED_MAIN_RESTORE_WINDOW_SHA256:
        raise DiscPatchError(
            f"{entry.path} does not contain the verified NTSC-U MDEC/VLC window.\n"
            f"Expected SHA-256: {EXPECTED_MAIN_RESTORE_WINDOW_SHA256}\n"
            f"Actual SHA-256:   {actual_window_hash}"
        )

    if MAIN_RESTORE_FILE_OFFSET % 2048 or MAIN_RESTORE_SIZE % 2048:
        raise AssertionError("The DP6 restore source must be sector aligned.")
    restore_lba = entry.lba + MAIN_RESTORE_SECTOR_OFFSET
    return entry, restore_lba


def patch_disc(cue_path: Path) -> tuple[Path, Path, str, int]:
    track, cue_text = parse_single_bin_mode2_cue(cue_path)
    with Mode2SectorReader(track.file_path, track.index01_frames) as sectors:
        iso = Iso9660(sectors)
        candidates = [
            entry
            for path, entry in iso.files.items()
            if path == "BATTLE/BATTLE.X" or path.endswith("/BATTLE/BATTLE.X")
        ]
        if len(candidates) != 1:
            raise DiscPatchError(
                f"Expected exactly one BATTLE/BATTLE.X; found {len(candidates)}."
            )
        entry = candidates[0]
        clean_battle = sectors.read_extent(entry.lba, entry.size)
        clean_hicloud = sectors.read_extent(HICLOUD_LBA, HICLOUD_SIZE)
        main_entry, restore_lba = find_verified_main_executable(iso, sectors)

    try:
        validate_retail_battle(clean_battle, "Disc BATTLE.X")
    except ValueError as exc:
        raise DiscPatchError(str(exc)) from exc

    if sha256(clean_hicloud) != ORIGINAL_HICLOUD_SHA256:
        raise DiscPatchError(
            "Disc HICLOUD.LZS is not the verified native NTSC-U archive.\n"
            f"Expected SHA-256: {ORIGINAL_HICLOUD_SHA256}\n"
            f"Actual SHA-256:   {sha256(clean_hicloud)}"
        )
    palette_patch_path = Path(__file__).resolve().parent / PALETTE_PATCH_NAME
    if not palette_patch_path.is_file():
        raise DiscPatchError(f"Palette64 patch is missing: {palette_patch_path}")
    try:
        patched_hicloud = apply_bps(
            clean_hicloud,
            base64.b64decode(palette_patch_path.read_text(encoding="ascii")),
        )
    except ValueError as exc:
        raise DiscPatchError(f"Palette64 patch validation failed: {exc}") from exc
    if (
        len(patched_hicloud) != HICLOUD_SIZE
        or sha256(patched_hicloud) != PATCHED_HICLOUD_SHA256
    ):
        raise DiscPatchError("Palette64 patch produced an unexpected HICLOUD.LZS.")

    disc_number = DISC_NUMBER_BY_EXECUTABLE[main_entry.path]
    output_stem = f"Final Fantasy VII (Disc {disc_number})_HighRes_Cloud"
    output_bin = cue_path.with_name(f"{output_stem}.bin")
    output_cue = cue_path.with_name(f"{output_stem}.cue")
    if output_bin.exists() or output_cue.exists():
        raise DiscPatchError(
            "Output already exists. Move or delete the previous "
            f"{output_stem}.bin/.cue before rerunning."
        )

    temporary_bin = output_bin.with_suffix(output_bin.suffix + ".tmp")
    try:
        shutil.copy2(track.file_path, temporary_bin)
        patched_battle_path = cue_path.parent / ".BATTLE_HICLOUD_DP6.tmp"
        try:
            patched_sha, _ = patch_file_from_bytes(
                clean_battle,
                patched_battle_path,
                restore_lba,
            )
            patched_battle = patched_battle_path.read_bytes()
        finally:
            patched_battle_path.unlink(missing_ok=True)
        write_iso_extent(temporary_bin, track.index01_frames, entry, patched_battle)
        write_iso_extent(
            temporary_bin,
            track.index01_frames,
            IsoFile("NATIVE/HICLOUD.LZS", HICLOUD_LBA, HICLOUD_SIZE),
            patched_hicloud,
        )

        with Mode2SectorReader(temporary_bin, track.index01_frames) as verify_sectors:
            verify_iso = Iso9660(verify_sectors)
            verified_entry = verify_iso.files[entry.path]
            verified = verify_sectors.read_extent(verified_entry.lba, verified_entry.size)
            verified_hicloud = verify_sectors.read_extent(HICLOUD_LBA, HICLOUD_SIZE)
            verified_main_entry, verified_restore_lba = find_verified_main_executable(
                verify_iso,
                verify_sectors,
            )
        if sha256(verified) != patched_sha:
            raise DiscPatchError("Disc read-back verification failed.")
        if sha256(verified_hicloud) != PATCHED_HICLOUD_SHA256:
            raise DiscPatchError("HICLOUD.LZS read-back verification failed.")
        if (
            verified_main_entry != main_entry
            or verified_restore_lba != restore_lba
        ):
            raise DiscPatchError("Executable restore-source read-back verification failed.")

        temporary_bin.replace(output_bin)
        escaped_original = re.escape(track.filename_text)
        output_cue_text, replacements = re.subn(
            rf'(?im)^(\s*FILE\s+)(?:"{escaped_original}"|{escaped_original})(\s+\S+.*)$',
            lambda match: f'{match.group(1)}"{output_bin.name}"{match.group(2)}',
            cue_text,
        )
        if replacements < 1:
            raise DiscPatchError("Could not update the output CUE's FILE line.")
        output_cue.write_text(output_cue_text, encoding="utf-8", newline="\n")
    except Exception:
        temporary_bin.unlink(missing_ok=True)
        output_bin.unlink(missing_ok=True)
        output_cue.unlink(missing_ok=True)
        raise

    return output_bin, output_cue, patched_sha, restore_lba


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def validate_retail_battle(raw: bytes, source_label: str = "BATTLE.X") -> bytes:
    """Validate the executable payload, not one particular gzip byte stream."""
    packed_digest = sha256(raw)
    if len(raw) < 18 or raw[8:10] != b"\x1f\x8b":
        raise ValueError(
            f"{source_label} does not contain the expected FFVII gzip stream.\n"
            f"File size:       {len(raw)} bytes\n"
            f"Packed SHA-256:  {packed_digest}"
        )
    try:
        decompressed = gzip.decompress(raw[8:])
    except (EOFError, OSError) as exc:
        raise ValueError(
            f"{source_label} contains an invalid or truncated gzip stream.\n"
            f"File size:       {len(raw)} bytes\n"
            f"Packed SHA-256:  {packed_digest}"
        ) from exc

    decompressed_digest = sha1(decompressed)
    if decompressed_digest != EXPECTED_DECOMPRESSED_SHA1:
        raise ValueError(
            f"{source_label} is not the verified NTSC-U retail executable revision.\n"
            f"File size:                  {len(raw)} bytes\n"
            f"Packed SHA-256:             {packed_digest}\n"
            f"Decompressed size:          {len(decompressed)} bytes\n"
            f"Expected decompressed SHA-1: {EXPECTED_DECOMPRESSED_SHA1}\n"
            f"Actual decompressed SHA-1:   {decompressed_digest}\n"
            "This can indicate another game revision or a previously patched image."
        )
    return decompressed


def words(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def patch_exact(image: bytearray, address: int, expected: bytes, replacement: bytes) -> None:
    if len(expected) != len(replacement):
        raise ValueError(f"Patch at 0x{address:08X} changes decompressed length.")
    offset = address - LOAD_ADDRESS
    actual = bytes(image[offset : offset + len(expected)])
    if actual != expected:
        raise ValueError(
            f"Unexpected bytes at 0x{address:08X}: "
            f"expected {expected.hex()}, found {actual.hex()}."
        )
    image[offset : offset + len(replacement)] = replacement


def build_patched_decompressed(source: bytes, restore_lba: int) -> bytes:
    if not 0 <= restore_lba <= 0xFFFFFFFF:
        raise ValueError(f"Invalid executable restore LBA: {restore_lba}.")
    image = bytearray(source)

    # Redirect the standard Cloud archive record to the retail HICLOUD.LZS
    # location and sector-rounded read length.
    patch_exact(
        image,
        0x800E8068,
        bytes.fromhex("7375000000080100"),
        bytes.fromhex("b577000000880100"),
    )

    # DP1-HF3 planned the party incrementally in its asynchronous callbacks,
    # then changed D_800F8390 only after the last party model arrived. FFVII
    # has already decompressed and initialized its enemies by then. In the HF3
    # crash fixture Barret consequently overwrote 0x2C30 bytes of the first
    # enemy at the original 0x80130200 base.
    #
    # DP6 retains DP2's proven pre-enemy load ordering. For a three-person
    # HiCloud party, the selector's third sorted non-empty record is an
    # ordinary model (IDs 1..15), so DP6 places that one body in the verified
    # 0x80052800-0x80062000 MDEC/VLC window and leaves only two party bodies in
    # the actor arena. The table is restored from the clean boot executable
    # after BATRES returns.
    planner_address = 0x800D2378
    results_restore_wrapper_address = 0x800D2498
    size_table_address = 0x800D24E8
    restore_flag_address = 0x800D252C
    planner_call = 0x0C000000 | ((planner_address & 0x0FFFFFFF) >> 2)

    # Preserve func_800B3A04's frame, call the planner, then invoke the retail
    # enemy decompressor with D_800F8390 already set to the packed party end.
    patch_exact(
        image,
        0x800B3A08,
        words(
            [
                0x00002021,
                0xAFB00010,
                0x3C108010,
                0x26108390,
                0x3C028013,
                0x24420200,
                0xAFBF0014,
                0x0C02D74E,
                0xAE020000,
            ]
        ),
        words(
            [
                0xAFBF0014,  # sw    ra,0x14(sp)
                0xAFB00010,  # sw    s0,0x10(sp)
                0x3C108010,  # lui   s0,0x8010
                0x26108390,  # addiu s0,s0,-0x7C70 ; &D_800F8390
                planner_call,
                0x00000000,  # nop (JAL delay)
                0x00002021,  # addu  a0,zero,zero
                0x0C02D74E,  # jal   func_800B5D38
                0x00000000,  # nop (JAL delay)
            ]
        ),
    )

    # The planner owns D_800F8384[0..2]. Prevent all three later callbacks
    # from replacing those addresses with 0x80103200 + slot * 0xF000.
    callback_address_blocks = (
        (
            0x800B3ACC,
            [
                0x86030000,
                0x00000000,
                0x00032080,
                0x00031100,
                0x00431023,
                0x00021300,
                0x3C038010,
                0x24633200,
                0x00431021,
                0x3C018010,
                0x24218384,
                0x00240821,
                0xAC220000,
            ],
        ),
        (
            0x800B3B98,
            [
                0x86030000,
                0x00000000,
                0x00032080,
                0x00031100,
                0x00431023,
                0x00021300,
                0x3C038010,
                0x24633200,
                0x00431021,
                0x3C018010,
                0x24218384,
                0x00240821,
                0xAC220000,
            ],
        ),
        (
            0x800B3C64,
            [
                0x86030000,
                0x00000000,
                0x00032080,
                0x00031100,
                0x00431023,
                0x00021300,
                0x3C038010,
                0x24633200,
                0x00431021,
                0x3C018010,
                0x24218384,
                0x00240821,
                0xAC220000,
            ],
        ),
    )
    for address, expected_words in callback_address_blocks:
        patch_exact(
            image,
            address,
            words(expected_words),
            b"\x00" * (len(expected_words) * 4),
        )

    # func_800B5C1C: consume the loader's pointer table instead of deriving
    # 0x80103200 + slot * 0xF000 again during the body copy.
    patch_exact(
        image,
        0x800B5C30,
        words([0x00102100, 0x00902023, 0x00042300, 0x3C038010, 0x24633200]),
        words([0x00102080, 0x3C038010, 0x00641821, 0x8C648384, 0x00000000]),
    )
    patch_exact(image, 0x800B5C4C, words([0x00832021]), words([0x00000000]))

    # func_800CCA68 later rebuilt the same pointer using slot * 0xF000 even
    # though the asynchronous loader had already populated D_800F8384.
    # Reload and preserve that existing per-slot pointer, including gaps.
    patch_exact(
        image,
        0x800CCAE4,
        words([0x00101100, 0x00501023, 0x00021300, 0x00431021]),
        words([0x8E220000, 0x00000000, 0x00000000, 0x00000000]),
    )

    # The battle entrypoint normally calls the BATRES overlay directly after
    # the battle loop. Route that one call through a wrapper. BATRES completes
    # first, so no actor can still consume the temporary model; the wrapper
    # then restores the original 31 executable sectors before field control.
    results_restore_wrapper_call = 0x0C000000 | (
        (results_restore_wrapper_address & 0x0FFFFFFF) >> 2
    )
    patch_exact(
        image,
        0x800A172C,
        words([0x0C06C000]),  # jal 0x801B0000 (BATRES entrypoint)
        words([results_restore_wrapper_call]),
    )

    # Retail func_800D1530 deliberately executes a 0x1000-byte NOP span before
    # returning. Direct calls enter the injected planner and wrapper below. A
    # guard on the ordinary path skips the complete injected code/data tail.
    skip_address = 0x800D2530
    skip_jump = 0x08000000 | ((skip_address & 0x0FFFFFFF) >> 2)
    patch_exact(
        image,
        0x800D2370,
        words([0x00000000, 0x00000000]),
        words([skip_jump, 0x00000000]),
    )

    planner = words(
        [
            # Build FFVII's own three (archive ID, physical slot) records now,
            # before its normal later invocation. The retail selector sorts
            # them by archive ID, preserving variant and physical-slot rules.
            0x27BDFFF8,  # addiu sp,sp,-8
            0xAFBF0004,  # sw    ra,4(sp)
            0x0C0317A5,  # jal   func_800C5E94
            0x00000000,  # nop (JAL delay)
            0x3C088010,  # lui   t0,0x8010
            0x2508A9C4,  # addiu t0,t0,-0x563C  ; selected model/slot pairs
            0x3C098010,  # lui   t1,0x8010
            0x25298384,  # addiu t1,t1,-0x7C7C  ; D_800F8384
            0x3C0A8010,  # lui   t2,0x8010
            0x254A3200,  # addiu t2,t2,0x3200   ; packed arena cursor
            0x340B0003,  # ori   t3,zero,3      ; three pair records
            0x3C0C800D,  # lui   t4,0x800D
            0x358C24E8,  # ori   t4,t4,0x24E8   ; body-size table
            0x00002021,  # addu  a0,zero,zero   ; has HiCloud flag
            0x3C03800D,  # lui   v1,0x800D
            0xAC60252C,  # sw    zero,0x252C(v1); restore-needed flag
            0xAD200000,  # sw    zero,0(t1)
            0xAD200004,  # sw    zero,4(t1)
            0xAD200008,  # sw    zero,8(t1)
            # loop @ 0x800D23C4
            0x950D0000,  # lhu   t5,0(t0)       ; archive ID
            0x950E0002,  # lhu   t6,2(t0)       ; physical slot/load delay
            0x340F00C8,  # ori   t7,zero,0xC8   ; empty sentinel
            0x11AF0026,  # beq   t5,t7,next
            0x00000000,  # nop (branch delay)
            0x11A00003,  # beq   t5,zero,mark_hicloud
            0x34020010,  # ori   v0,zero,16     ; native HICLOUD ID
            0x15A20002,  # bne   t5,v0,check_temp
            0x00000000,  # nop (branch delay)
            # mark_hicloud @ 0x800D23E8
            0x34040001,  # ori   a0,zero,1
            # check_temp @ 0x800D23EC
            0x34020001,  # ori   v0,zero,1
            0x15620010,  # bne   t3,v0,main_arena
            0x00000000,  # nop (branch delay)
            0x1080000E,  # beq   a0,zero,main_arena
            0x00000000,  # nop (branch delay)
            # Only IDs 1..15 fit the 0xF800 temporary window. In ordinary
            # HiCloud parties, redirected ID 0 sorts first and this full-party
            # third record is therefore one of those ordinary models.
            0x11A0000C,  # beq   t5,zero,main_arena
            0x34020010,  # ori   v0,zero,16
            0x11A2000A,  # beq   t5,v0,main_arena
            0x000E7880,  # sll   t7,t6,2        ; branch delay
            0x012F7821,  # addu  t7,t1,t7
            0x3C028005,  # lui   v0,0x8005
            0x34422800,  # ori   v0,v0,0x2800   ; 0x80052800
            0xADE20000,  # sw    v0,0(t7)       ; split physical-slot pointer
            0x3C0F800D,  # lui   t7,0x800D
            0x34020001,  # ori   v0,zero,1
            0xADE2252C,  # sw    v0,0x252C(t7)  ; restore after BATRES
            0x0803491B,  # j     next (0x800D246C)
            0x00000000,  # nop (jump delay)
            # main_arena @ 0x800D2434
            0x000E7880,  # sll   t7,t6,2
            0x012F7821,  # addu  t7,t1,t7
            0xADEA0000,  # sw    t2,0(t7)       ; pointer for physical slot
            0x2DA20011,  # sltiu v0,t5,17
            0x10400007,  # beq   v0,zero,unknown
            0x000D7880,  # sll   t7,t5,2        ; branch delay
            0x018F7821,  # addu  t7,t4,t7
            0x8DE20000,  # lw    v0,0(t7)       ; packed body extent
            0x00000000,  # nop (load delay)
            0x01425021,  # addu  t2,t2,v0
            0x0803491B,  # j     next (0x800D246C)
            0x00000000,  # nop (jump delay)
            # Unknown IDs retain one vanilla 0xF000 reservation.
            0x3402F000,  # ori   v0,zero,0xF000
            0x01425021,  # addu  t2,t2,v0
            # next @ 0x800D246C
            0x25080004,  # addiu t0,t0,4
            0x256BFFFF,  # addiu t3,t3,-1
            0x1D60FFD3,  # bgtz  t3,loop
            0x00000000,  # nop (branch delay)
            0x3C088010,  # lui   t0,0x8010
            0xAD0A8390,  # sw    t2,-0x7C70(t0) ; enemy base before load
            0x8FBF0004,  # lw    ra,4(sp)
            0x27BD0008,  # addiu sp,sp,8        ; fill RA load delay
            0x03E00008,  # jr    ra
            0x00000000,  # nop (JR delay)
        ]
    )
    patch_exact(image, planner_address, b"\x00" * len(planner), planner)

    results_restore_wrapper = words(
        [
            0x27BDFFE8,  # addiu sp,sp,-0x18
            0xAFBF0010,  # sw    ra,0x10(sp)
            0x0C06C000,  # jal   0x801B0000     ; run BATRES first
            0x00000000,  # nop (JAL delay)
            0x3C08800D,  # lui   t0,0x800D
            0x8D08252C,  # lw    t0,0x252C(t0)  ; restore-needed flag
            0x00000000,  # nop (load delay)
            0x11000008,  # beq   t0,zero,done
            0x00000000,  # nop (branch delay)
            0x3C040000 | ((restore_lba >> 16) & 0xFFFF),
            0x34840000 | (restore_lba & 0xFFFF),
            0x3405F800,  # ori   a1,zero,0xF800 ; 31 sectors
            0x3C068005,  # lui   a2,0x8005
            0x34C62800,  # ori   a2,a2,0x2800   ; 0x80052800
            # DP5 used func_80033FC4 here. That is the game's streaming
            # decompression reader, not a raw sector copy: it interpreted the
            # executable's MDEC/VLC bytes as compressed data and expanded
            # through the timer globals at 0x80062B94. Use the synchronous raw
            # reader so exactly a1 bytes are restored to a2.
            0x0C00CFD0,  # jal   func_80033F40 ; synchronous raw sector read
            0x00003821,  # addu  a3,zero,zero   ; no callback, JAL delay
            # done @ 0x800D24D8
            0x8FBF0010,  # lw    ra,0x10(sp)
            0x27BD0018,  # addiu sp,sp,0x18     ; fill RA load delay
            0x03E00008,  # jr    ra
            0x00000000,  # nop (JR delay)
        ]
    )
    patch_exact(
        image,
        results_restore_wrapper_address,
        b"\x00" * len(results_restore_wrapper),
        results_restore_wrapper,
    )

    # Table indices are the retail D_800E8068 archive IDs. Index 0 is changed
    # from CLOUD to HICLOUD by the resource redirect above; index 9 remains the
    # duplicate retail CLOUD record. Values are the offset-table -17 body
    # extents copied to main RAM after texture extraction.
    player_body_sizes = (
        0x167F4,  #  0 HICLOUD (redirected CLOUD record)
        0x0BE30,  #  1 BARRETT
        0x0DB54,  #  2 TIFA
        0x0DE3C,  #  3 EARITH
        0x0EED8,  #  4 RED13
        0x0E6C4,  #  5 YUFI
        0x0EAD4,  #  6 KETCY
        0x0EB88,  #  7 VINSENT
        0x0D19C,  #  8 CID
        0x0B408,  #  9 duplicate retail CLOUD
        0x0C8DC,  # 10 SEFIROS
        0x0B8E8,  # 11 BARRETT2
        0x0B714,  # 12 BARRETT3
        0x0B77C,  # 13 BARRETT4
        0x0EC9C,  # 14 VINSENT2
        0x0EDB4,  # 15 VINSENT3
        0x167F4,  # 16 native HICLOUD record
    )
    size_table = words(list(player_body_sizes))
    patch_exact(
        image,
        size_table_address,
        b"\x00" * len(size_table),
        size_table,
    )
    return bytes(image)


def patch_file_from_bytes(
    raw: bytes,
    output_path: Path,
    restore_lba: int,
) -> tuple[str, str]:
    decompressed = validate_retail_battle(raw, "Input BATTLE.X")

    patched = build_patched_decompressed(decompressed, restore_lba)
    stream = gzip.compress(patched, compresslevel=9, mtime=0)
    rebuilt = raw[:8] + stream
    if len(rebuilt) > len(raw):
        raise ValueError(
            f"Patched compressed file grew by {len(rebuilt) - len(raw)} bytes; "
            "refusing to change the disc-file allocation."
        )
    rebuilt += b"\x00" * (len(raw) - len(rebuilt))
    if gzip.decompress(rebuilt[8:]) != patched:
        raise AssertionError("Internal gzip round-trip verification failed.")

    output_path.write_bytes(rebuilt)
    return sha256(rebuilt), sha1(patched)


def patch_file(
    source_path: Path,
    output_path: Path,
    restore_lba: int,
) -> tuple[str, str]:
    return patch_file_from_bytes(source_path.read_bytes(), output_path, restore_lba)


def main() -> int:
    if len(sys.argv) not in (2, 3, 4):
        print(
            "Usage:\n"
            "  patch_hicloud_dp6.py CLEAN_DISC.cue\n"
            "  patch_hicloud_dp6.py CLEAN_BATTLE.X RESTORE_LBA "
            "[OUTPUT_BATTLE.X]\n\n"
            "Use CUE mode unless you are deliberately rebuilding BATTLE.X; "
            "CUE mode derives and verifies RESTORE_LBA automatically."
        )
        return 2
    source = Path(sys.argv[1]).resolve()
    if source.suffix.lower() == ".cue":
        if len(sys.argv) != 2:
            print("A custom output path is not supported for CUE patching.")
            return 2
        print("Verifying the embedded executable and copying the clean BIN...")
        try:
            output_bin, output_cue, battle_sha, restore_lba = patch_disc(source)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 1
        print(f"Created BIN: {output_bin}")
        print(f"Created CUE: {output_cue}")
        print(f"Embedded BATTLE.X SHA-256: {battle_sha}")
        print(f"Embedded Palette64 HICLOUD.LZS SHA-256: {PATCHED_HICLOUD_SHA256}")
        print(f"Verified MDEC/VLC restore source LBA: 0x{restore_lba:X}")
        print("The original BIN/CUE were not modified.")
        return 0

    if len(sys.argv) not in (3, 4):
        print(
            "Standalone BATTLE.X mode requires the verified restore-sector LBA. "
            "Use CUE mode to derive it automatically."
        )
        return 2
    try:
        restore_lba = int(sys.argv[2], 0)
    except ValueError:
        print(f"Invalid RESTORE_LBA: {sys.argv[2]}")
        return 2
    output = (
        Path(sys.argv[3]).resolve()
        if len(sys.argv) == 4
        else source.with_name("BATTLE_HICLOUD_DP6.X")
    )
    if source == output:
        print("Refusing to overwrite the clean source file.")
        return 2
    try:
        output_sha256, decompressed_sha1 = patch_file(source, output, restore_lba)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Created: {output}")
    print(f"Patched file SHA-256:       {output_sha256}")
    print(f"Patched decompressed SHA-1: {decompressed_sha1}")
    print("The original BATTLE.X was not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

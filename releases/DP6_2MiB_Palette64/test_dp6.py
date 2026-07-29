#!/usr/bin/env python3
"""Regression tests for the DP6 raw-restore/Palette64 BIN/CUE patch path."""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import struct
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import patch_hicloud_dp6 as dp6


PACKAGE = Path(__file__).resolve().parent


def find_fixture_root() -> Path:
    for parent in PACKAGE.parents:
        if (parent / "project_sources" / "02-BATTLE.X").is_file():
            return parent
    return PACKAGE


ROOT = find_fixture_root()
SOURCE_BATTLE = ROOT / "project_sources" / "02-BATTLE.X"
SOURCE_HICLOUD = ROOT / "project_sources" / "09-HICLOUD-1-.LZS"
HF2_CRASH_DUMP = (
    ROOT
    / "upload"
    / "RamDump_2_Final Fantasy VII (Disc 3)_HiCloud_DP1_HF2_Palette64.bin"
)
HF3_CRASH_DUMP = (
    ROOT
    / "upload"
    / "RamDump_Final Fantasy VII (Disc 3)_HiCloud_DP1_HF3_Palette64.bin"
)
DP5_REGULAR_RESULTS_CRASH_DUMP = (
    ROOT
    / "upload"
    / "RamDump_Regular_Final Fantasy VII (Disc 3)_HiCloud_DP5_Palette64.bin"
)
DP5_ZOLOM_RESULTS_CRASH_DUMP = (
    ROOT
    / "upload"
    / "RamDump_Zolom_Final Fantasy VII (Disc 3)_HiCloud_DP5_Palette64.bin"
)
MODEL_REPORT = ROOT / "upload" / "enemy_models(1).csv"
LAYOUT_REPORT = ROOT / "upload" / "layout_rankings(1).csv"
VANILLA_RAM_DUMP = (
    ROOT / "project_sources" / "11-Vanilla_Cloud__Empty_Barrett_RamDump.bin"
)
HICLOUD_RAM_DUMP = (
    ROOT / "project_sources" / "12-HiCloud__Empty_Barrett_RamDump.bin"
)
KOTR_SNAPSHOTS = tuple(sorted((ROOT / "upload").glob("KOTR_*.bin")))
TRACE_COVERAGE_FLAGS = (
    ROOT
    / "analysis"
    / "evidence"
    / "FF7_Battle_RAM_Evidence_20260728_164647"
    / "combined_coverage_flags.bin"
)
REFERENCE_RESTORE_LBA = 0x12345
SYNTHETIC_MAIN_LBA = 100


PLAYER_BODY_SIZES = (
    0x167F4,  #  0 HICLOUD (redirected CLOUD record)
    0x0BE30,  #  1 BARRETT
    0x0DB54,  #  2 TIFA
    0x0DE3C,  #  3 EARITH
    0x0EED8,  #  4 RED13
    0x0E6C4,  #  5 YUFI
    0x0EAD4,  #  6 KETCY
    0x0EB88,  #  7 VINSENT
    0x0D19C,  #  8 CID
    0x0B408,  #  9 duplicate CLOUD
    0x0C8DC,  # 10 SEFIROS
    0x0B8E8,  # 11 BARRETT2
    0x0B714,  # 12 BARRETT3
    0x0B77C,  # 13 BARRETT4
    0x0EC9C,  # 14 VINSENT2
    0x0EDB4,  # 15 VINSENT3
    0x167F4,  # 16 native HICLOUD record
)


def plan_layout(
    pairs: list[tuple[int, int]],
) -> tuple[tuple[int, int, int], int, bool]:
    pointers = [0, 0, 0]
    cursor = dp6.ACTOR_ARENA_BASE
    has_hicloud = False
    restore_needed = False
    for record_index, (model_id, physical_slot) in enumerate(pairs):
        if model_id == 0xC8:
            continue
        if model_id in (0, 16):
            has_hicloud = True
        if record_index == 2 and has_hicloud and 0 < model_id < 16:
            pointers[physical_slot] = dp6.TEMP_MODEL_BASE
            restore_needed = True
            continue
        pointers[physical_slot] = cursor
        cursor += (
            PLAYER_BODY_SIZES[model_id] if model_id < len(PLAYER_BODY_SIZES) else 0xF000
        )
    return tuple(pointers), cursor, restore_needed


def both32(value: int) -> bytes:
    return struct.pack("<I", value) + struct.pack(">I", value)


def both16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def directory_record(lba: int, size: int, name: bytes, is_dir: bool) -> bytes:
    name_padding = b"\x00" if len(name) % 2 == 0 else b""
    length = 33 + len(name) + len(name_padding)
    record = bytearray(length)
    record[0] = length
    record[2:10] = both32(lba)
    record[10:18] = both32(size)
    record[25] = 2 if is_dir else 0
    record[28:32] = both16(1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def raw_mode2_sector(payload: bytes, sector_number: int) -> bytes:
    def bcd(value: int) -> int:
        return ((value // 10) << 4) | (value % 10)

    sector = bytearray(2352)
    sector[:12] = b"\x00" + b"\xFF" * 10 + b"\x00"
    absolute_frame = sector_number + 150
    mm, remainder = divmod(absolute_frame, 75 * 60)
    ss, ff = divmod(remainder, 75)
    sector[12:16] = bytes((bcd(mm), bcd(ss), bcd(ff), 2))
    sector[16:24] = b"\x00\x00\x08\x00" * 2
    sector[24 : 24 + len(payload)] = payload
    dp6.regenerate_mode2_form1_checksums(sector)
    return bytes(sector)


def build_synthetic_main() -> bytes:
    main = bytearray(dp6.MAIN_RESTORE_FILE_OFFSET + dp6.MAIN_RESTORE_SIZE)
    main[:8] = b"PS-X EXE"
    struct.pack_into("<I", main, 0x18, dp6.MAIN_RAM_LOAD_ADDRESS)
    struct.pack_into("<I", main, 0x1C, len(main) - dp6.PSX_EXE_HEADER_SIZE)
    main[
        dp6.MAIN_RESTORE_FILE_OFFSET :
    ] = bytes(
        (index * 73 + 19) & 0xFF for index in range(dp6.MAIN_RESTORE_SIZE)
    )
    return bytes(main)


def build_test_disc(
    directory: Path,
    battle: bytes,
    hicloud: bytes,
    main: bytes,
) -> tuple[Path, Path]:
    root_lba = 20
    battle_dir_lba = 21
    file_lba = 22
    file_sectors = (len(battle) + 2047) // 2048
    main_sectors = (len(main) + 2047) // 2048
    hicloud_sectors = (len(hicloud) + 2047) // 2048
    sector_count = max(
        file_lba + file_sectors + 1,
        SYNTHETIC_MAIN_LBA + main_sectors + 1,
        dp6.HICLOUD_LBA + hicloud_sectors + 1,
    )
    payloads = [bytearray(2048) for _ in range(sector_count)]

    pvd = payloads[16]
    pvd[0:7] = b"\x01CD001\x01"
    pvd[40:72] = b"FF7_DP6_TEST".ljust(32, b" ")
    pvd[80:88] = both32(sector_count)
    pvd[120:124] = both16(1)
    pvd[124:128] = both16(1)
    pvd[128:132] = both16(2048)
    root_record = directory_record(root_lba, 2048, b"\x00", True)
    pvd[156 : 156 + len(root_record)] = root_record
    payloads[17][0:7] = b"\xFFCD001\x01"

    root = payloads[root_lba]
    records = (
        directory_record(root_lba, 2048, b"\x00", True),
        directory_record(root_lba, 2048, b"\x01", True),
        directory_record(battle_dir_lba, 2048, b"BATTLE", True),
        directory_record(
            SYNTHETIC_MAIN_LBA,
            len(main),
            b"SCUS_941.65;1",
            False,
        ),
    )
    cursor = 0
    for record in records:
        root[cursor : cursor + len(record)] = record
        cursor += len(record)

    battle_dir = payloads[battle_dir_lba]
    records = (
        directory_record(battle_dir_lba, 2048, b"\x00", True),
        directory_record(root_lba, 2048, b"\x01", True),
        directory_record(file_lba, len(battle), b"BATTLE.X;1", False),
    )
    cursor = 0
    for record in records:
        battle_dir[cursor : cursor + len(record)] = record
        cursor += len(record)

    for index in range(file_sectors):
        chunk = battle[index * 2048 : (index + 1) * 2048]
        payloads[file_lba + index][: len(chunk)] = chunk
    for index in range(main_sectors):
        chunk = main[index * 2048 : (index + 1) * 2048]
        payloads[SYNTHETIC_MAIN_LBA + index][: len(chunk)] = chunk
    for index in range(hicloud_sectors):
        chunk = hicloud[index * 2048 : (index + 1) * 2048]
        payloads[dp6.HICLOUD_LBA + index][: len(chunk)] = chunk

    bin_path = directory / "clean.bin"
    cue_path = directory / "clean.cue"
    with bin_path.open("wb") as image:
        for sector_number, payload in enumerate(payloads):
            image.write(raw_mode2_sector(payload, sector_number))
    cue_path.write_text(
        'FILE "clean.bin" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
    )
    return bin_path, cue_path


@unittest.skipUnless(
    SOURCE_BATTLE.is_file() and SOURCE_HICLOUD.is_file(),
    "Supplied BATTLE.X/HICLOUD.LZS fixtures absent",
)
class Dp6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.clean = dp6.validate_retail_battle(SOURCE_BATTLE.read_bytes())
        cls.patched = dp6.build_patched_decompressed(
            cls.clean,
            REFERENCE_RESTORE_LBA,
        )

    @classmethod
    def words_at(cls, address: int, count: int) -> list[int]:
        offset = address - dp6.LOAD_ADDRESS
        return list(struct.unpack_from(f"<{count}I", cls.patched, offset))

    def test_hf3_crash_is_enemy_overwrite_not_party_rebase_race(self) -> None:
        if not HF2_CRASH_DUMP.is_file() or not HF3_CRASH_DUMP.is_file():
            self.skipTest("HF2/HF3 RAM fixtures absent")
        hf2 = HF2_CRASH_DUMP.read_bytes()
        hf3 = HF3_CRASH_DUMP.read_bytes()
        self.assertEqual(len(hf3), 2 * 1024 * 1024)

        tcb = 0xE29C
        self.assertEqual(struct.unpack_from("<I", hf3, tcb + 0x88)[0], 0x800D2AC0)
        self.assertEqual(struct.unpack_from("<I", hf3, tcb + 0x98)[0], 0x10000010)
        self.assertEqual(
            struct.unpack_from("<I", hf3, tcb + 0x08 + 4 * 4)[0],
            0x38002433,
        )
        self.assertEqual(
            struct.unpack_from("<I", hf3, tcb + 0x08 + 31 * 4)[0],
            0x800BA9CC,
        )

        # func_800D29D4 saved its caller's s7 at sp-0x24. func_800BA598
        # carries the actor index in s7; 4 is the first enemy actor.
        saved_sp = struct.unpack_from("<I", hf3, tcb + 0x08 + 29 * 4)[0]
        self.assertEqual(
            struct.unpack_from("<I", hf3, (saved_sp & 0x1FFFFF) - 0x24)[0],
            4,
        )

        # Party packing/rebasing did complete.
        self.assertEqual(
            struct.unpack_from("<4I", hf3, 0xF8384),
            (0x80103200, 0x80127548, 0x801199F4, 0x80132E30),
        )
        self.assertEqual(
            struct.unpack_from("<I", hf3, 0x127548 + 8)[0],
            0x8012DB38,
        )

        # But enemy 0 had already loaded at 0x80130200. Its next pointer was
        # calculated from that old base even though HF3 later rewrote only
        # D_800F8390 to 0x80132E30.
        self.assertEqual(struct.unpack_from("<I", hf3, 0xF8394)[0], 0x80135C94)
        self.assertEqual(0x80135C94 - 0x80130200, 0x5A94)
        self.assertEqual(0x80132E30 - 0x80130200, 0x2C30)

        # The same relocated party bytes occupy the entire overlap in both
        # crash captures even though their enemy payloads differ afterward.
        self.assertEqual(hf2[0x130200:0x132E30], hf3[0x130200:0x132E30])
        self.assertNotEqual(hf2[0x132E30:0x132E70], hf3[0x132E30:0x132E70])

    def test_planner_runs_before_enemy_decompression(self) -> None:
        hook = self.words_at(0x800B3A08, 9)
        planner_call = 0x0C000000 | ((0x800D2378 & 0x0FFFFFFF) >> 2)
        self.assertEqual(
            hook,
            [
                0xAFBF0014,
                0xAFB00010,
                0x3C108010,
                0x26108390,
                planner_call,
                0,
                0x00002021,
                0x0C02D74E,
                0,
            ],
        )
        planner = self.words_at(0x800D2378, 71)
        self.assertIn(0xAD0A8390, planner)
        self.assertLess(planner.index(0xAD0A8390), planner.index(0x03E00008))

        # The retail hardcoded enemy-base construction existed only here.
        code = self.patched[0x1158:0x47A38]
        words_in_code = struct.unpack(f"<{len(code) // 4}I", code)
        self.assertNotIn(0x3C028013, words_in_code)

    def test_planner_uses_retail_party_selection_before_later_normal_call(self) -> None:
        planner = self.words_at(0x800D2378, 71)
        retail_selector_call = 0x0C0317A5
        self.assertEqual(planner[2], retail_selector_call)
        # func_800B3D38's original call remains present and unchanged.
        self.assertEqual(self.words_at(0x800B3D40, 1), [retail_selector_call])

    def test_all_five_fixed_party_address_paths_are_removed(self) -> None:
        for address in (0x800B3ACC, 0x800B3B98, 0x800B3C64):
            self.assertEqual(self.words_at(address, 13), [0] * 13)

        self.assertEqual(
            self.words_at(0x800B5C30, 5),
            [0x00102080, 0x3C038010, 0x00641821, 0x8C648384, 0],
        )
        self.assertEqual(self.words_at(0x800B5C4C, 1), [0])
        self.assertEqual(
            self.words_at(0x800CCAE4, 4),
            [0x8E220000, 0, 0, 0],
        )

    def test_vanilla_completion_paths_are_restored(self) -> None:
        expected = [0x34020003, 0x3C018016, 0xA0226F64]
        for address in (0x800B3B64, 0x800B3C30, 0x800B3CB0):
            self.assertEqual(self.words_at(address, 3), expected)

        # DP6 is built from DP2, so neither DP3 nor DP4's packet-heap changes
        # remain in the renderer.
        self.assertEqual(self.words_at(0x800B8078, 1), [0x3C038018])
        self.assertEqual(self.words_at(0x800BBE04, 1), [0x14400013])

    def test_r3000a_load_delays_are_scheduled(self) -> None:
        def read_registers(word: int) -> set[int]:
            opcode = word >> 26
            rs = (word >> 21) & 31
            rt = (word >> 16) & 31
            if opcode == 0:
                return {rs, rt} - {0}
            if opcode in (2, 3, 15):  # J, JAL, LUI
                return set()
            if opcode in (4, 5):  # BEQ/BNE
                return {rs, rt} - {0}
            if opcode in (6, 7, 8, 9, 10, 11, 12, 13, 14):
                return {rs} - {0}
            if opcode in (40, 41, 42, 43, 46):  # stores read base and value
                return {rs, rt} - {0}
            return {rs} - {0}

        load_opcodes = {32, 33, 35, 36, 37}  # LB/LH/LW/LBU/LHU
        for address, count in (
            (0x800D2378, 71),
            (0x800D2498, 20),
            (0x800B5C30, 8),
            (0x800CCAE4, 5),
        ):
            block = self.words_at(address, count)
            for index, word in enumerate(block[:-1]):
                if word >> 26 in load_opcodes:
                    loaded_register = (word >> 16) & 31
                    self.assertNotIn(
                        loaded_register,
                        read_registers(block[index + 1]),
                        f"load-delay hazard at 0x{address + index * 4:08X}",
                    )

    def test_size_table_and_layouts(self) -> None:
        self.assertEqual(
            tuple(self.words_at(0x800D24E8, 17)),
            PLAYER_BODY_SIZES,
        )

        pointers, enemy_start, restore = plan_layout([(0, 0), (2, 2), (11, 1)])
        self.assertEqual(pointers, (0x80103200, dp6.TEMP_MODEL_BASE, 0x801199F4))
        self.assertEqual(enemy_start, 0x80127548)
        self.assertTrue(restore)

        pointers, enemy_start, restore = plan_layout(
            [(0, 0), (11, 2), (0xC8, 1)]
        )
        self.assertEqual(pointers, (0x80103200, 0, 0x801199F4))
        self.assertEqual(enemy_start, 0x801252DC)
        self.assertFalse(restore)

        pointers, enemy_start, restore = plan_layout([(0, 0), (4, 1), (15, 2)])
        self.assertEqual(
            pointers,
            (0x80103200, 0x801199F4, dp6.TEMP_MODEL_BASE),
        )
        self.assertEqual(enemy_start, 0x801288CC)
        self.assertTrue(restore)

        pointers, enemy_start, restore = plan_layout(
            [(0, 0), (99, 1), (0xC8, 2)]
        )
        self.assertEqual(pointers[1], 0x801199F4)
        self.assertEqual(enemy_start, 0x801289F4)
        self.assertFalse(restore)

    def test_planner_split_branch_targets(self) -> None:
        planner_address = 0x800D2378
        planner = self.words_at(planner_address, 71)

        def branch_target(index: int) -> int:
            immediate = planner[index] & 0xFFFF
            if immediate & 0x8000:
                immediate -= 0x10000
            return planner_address + (index + 1 + immediate) * 4

        def jump_target(index: int) -> int:
            pc = planner_address + index * 4
            return ((pc + 4) & 0xF0000000) | ((planner[index] & 0x03FFFFFF) << 2)

        self.assertEqual(branch_target(22), 0x800D246C)  # empty -> next
        self.assertEqual(branch_target(24), 0x800D23E8)  # ID 0 -> mark HiCloud
        self.assertEqual(branch_target(26), 0x800D23EC)  # non-ID16 -> check
        self.assertEqual(branch_target(30), 0x800D2434)  # first/second -> arena
        self.assertEqual(branch_target(32), 0x800D2434)  # no HiCloud -> arena
        self.assertEqual(branch_target(34), 0x800D2434)  # ID 0 never split
        self.assertEqual(branch_target(36), 0x800D2434)  # ID 16 never split
        self.assertEqual(jump_target(45), 0x800D246C)  # split model -> next
        self.assertEqual(branch_target(51), 0x800D2464)  # unknown fallback
        self.assertEqual(jump_target(57), 0x800D246C)  # known size -> next
        self.assertEqual(branch_target(63), 0x800D23C4)  # loop

        # Guard, planner, restore wrapper, table, and flag fit entirely inside
        # retail's NOP span.
        self.assertEqual(self.words_at(0x800D2370, 2), [0x0803494C, 0])
        self.assertEqual(self.words_at(0x800D252C, 1), [0])
        self.assertEqual(0x800D252C + 4, 0x800D2530)

    def test_packet_renderer_is_byte_identical_to_retail(self) -> None:
        # DP3/DP4 failed because they changed packet-bank ownership. DP6 must
        # retain the complete retail renderer and its two alternating banks.
        for address, length in (
            (0x800B8078, 0x40),
            (0x800BBDF8, 0x70),
            (0x800D8A88, 0x68),
        ):
            offset = address - dp6.LOAD_ADDRESS
            self.assertEqual(
                self.patched[offset : offset + length],
                self.clean[offset : offset + length],
            )

    def test_results_wrapper_restores_after_batres(self) -> None:
        wrapper_address = 0x800D2498
        wrapper_call = 0x0C000000 | (
            (wrapper_address & 0x0FFFFFFF) >> 2
        )
        self.assertEqual(self.words_at(0x800A172C, 1), [wrapper_call])
        wrapper = self.words_at(wrapper_address, 20)
        self.assertEqual(wrapper[2], 0x0C06C000)  # BATRES entrypoint
        self.assertEqual(wrapper[5], 0x8D08252C)  # restore-needed flag
        self.assertEqual(wrapper[7], 0x11000008)  # skip read when unused
        injected_lba = ((wrapper[9] & 0xFFFF) << 16) | (wrapper[10] & 0xFFFF)
        self.assertEqual(injected_lba, REFERENCE_RESTORE_LBA)
        self.assertEqual(wrapper[11], 0x3405F800)
        self.assertEqual(wrapper[12:14], [0x3C068005, 0x34C62800])
        self.assertEqual(wrapper[14], 0x0C00CFD0)  # func_80033F40 raw reader
        self.assertNotIn(0x0C00CFF1, wrapper)  # DP5's DS_read decompressor
        self.assertLess(wrapper.index(0x0C06C000), wrapper.index(0x0C00CFD0))

    def test_dp5_results_crashes_prove_decompression_reader_overrun(self) -> None:
        if not (
            DP5_REGULAR_RESULTS_CRASH_DUMP.is_file()
            and DP5_ZOLOM_RESULTS_CRASH_DUMP.is_file()
        ):
            self.skipTest("DP5 results-screen crash fixtures absent")

        regular = DP5_REGULAR_RESULTS_CRASH_DUMP.read_bytes()
        zolom = DP5_ZOLOM_RESULTS_CRASH_DUMP.read_bytes()
        self.assertEqual(len(regular), 2 * 1024 * 1024)
        self.assertEqual(len(zolom), 2 * 1024 * 1024)

        # Both unrelated encounters reach the same SDK timer read with the
        # same invalid timer-register base. The active BIOS TCB stores GPRs at
        # +0x08, EPC at +0x88, and Cause at +0x98.
        tcb = 0xE29C
        for dump in (regular, zolom):
            self.assertEqual(
                struct.unpack_from("<I", dump, tcb + 0x88)[0],
                0x80042C80,
            )
            self.assertEqual(
                struct.unpack_from("<I", dump, tcb + 0x98)[0],
                0x0000041C,
            )
            self.assertEqual(
                struct.unpack_from("<I", dump, tcb + 0x08 + 3 * 4)[0],
                0x00FE091E,
            )
            self.assertEqual(
                struct.unpack_from("<I", dump, 0x42C80)[0],
                0x94620000,  # lhu v0,0(v1) in GetRCnt
            )
            self.assertEqual(
                struct.unpack_from("<I", dump, 0x62B94)[0],
                0x00FE08FE,
            )
            self.assertEqual(
                struct.unpack_from("<I", dump, 0xD2498 + 14 * 4)[0],
                0x0C00CFF1,  # DP5 called the decompression reader
            )

        # The corruption is deterministic, begins exactly at DP5's restore
        # destination, and continues 0x1350 bytes past the requested 0xF800
        # raw window into the SDK timer globals.
        self.assertEqual(regular[0x51A4C:0x52800], zolom[0x51A4C:0x52800])
        self.assertEqual(regular[0x52800:0x63350], zolom[0x52800:0x63350])
        self.assertEqual(
            hashlib.sha256(regular[0x52800:0x63350]).hexdigest(),
            "3adb418466f6b6847113d6ad52e246f205e7ca253e5b235e00bd5378eaf77554",
        )

    def test_retail_reader_contracts_confirm_dp6_uses_raw_path(self) -> None:
        retail_dump = ROOT / "upload" / "KOTR_06_POST_BATTLE_RESULTS_SCREEN.bin"
        if not retail_dump.is_file():
            self.skipTest("Retail main-RAM code fixture absent")
        ram = retail_dump.read_bytes()

        def word(address: int) -> int:
            return struct.unpack_from("<I", ram, address & 0x1FFFFF)[0]

        # func_80033F40 invokes the ordinary CDOP_3 reader. By contrast,
        # func_80033FC4 invokes DS_read, whose setup explicitly installs the
        # streaming decompressor at func_80034D2C.
        self.assertEqual(word(0x80033F74), 0x0C00CF8D)  # -> func_80033E34
        self.assertEqual(word(0x80033FF8), 0x0C00CF9D)  # -> DS_read
        self.assertEqual(word(0x80033EB8), 0x0C00D34B)  # -> func_80034D2C
        self.assertEqual(word(0x80033E44), 0x34040003)  # CDOP_3
        self.assertEqual(word(0x80033E8C), 0x3404000B)  # CDOP_11 / DS_read

    def test_only_declared_executable_ranges_change(self) -> None:
        declared_ranges = (
            (0x800A172C, 4),
            (0x800B3A08, 9 * 4),
            (0x800B3ACC, 13 * 4),
            (0x800B3B98, 13 * 4),
            (0x800B3C64, 13 * 4),
            (0x800B5C30, 5 * 4),
            (0x800B5C4C, 4),
            (0x800CCAE4, 4 * 4),
            (0x800D2370, 2 * 4),
            (0x800D2378, 71 * 4),
            (0x800D2498, 20 * 4),
            (0x800D24E8, 17 * 4),
            (0x800E8068, 8),
        )
        for offset, (clean_byte, patched_byte) in enumerate(
            zip(self.clean, self.patched)
        ):
            if clean_byte == patched_byte:
                continue
            address = dp6.LOAD_ADDRESS + offset
            self.assertTrue(
                any(start <= address < start + length for start, length in declared_ranges),
                f"undeclared change at 0x{address:08X}",
            )

    def test_mdec_window_is_untouched_in_trace_and_all_ram_captures(self) -> None:
        captures = tuple(
            path
            for path in (VANILLA_RAM_DUMP, HICLOUD_RAM_DUMP, *KOTR_SNAPSHOTS)
            if path.is_file()
        )
        if len(captures) < 13 or not TRACE_COVERAGE_FLAGS.is_file():
            self.skipTest("Trace-coverage or complete RAM-capture fixtures absent")

        expected_hash = dp6.EXPECTED_MAIN_RESTORE_WINDOW_SHA256
        start = dp6.TEMP_MODEL_BASE & 0x1FFFFF
        end = dp6.TEMP_MODEL_LIMIT & 0x1FFFFF
        for path in captures:
            dump = path.read_bytes()
            self.assertEqual(len(dump), 2 * 1024 * 1024, path.name)
            self.assertEqual(
                hashlib.sha256(dump[start:end]).hexdigest(),
                expected_hash,
                path.name,
            )

        coverage = TRACE_COVERAGE_FLAGS.read_bytes()
        self.assertEqual(len(coverage), 2 * 1024 * 1024)
        self.assertEqual(coverage[start:end], b"\x00" * dp6.TEMP_MODEL_CAPACITY)

        largest_ordinary_body = max(PLAYER_BODY_SIZES[1:16])
        self.assertEqual(largest_ordinary_body, 0xEED8)
        self.assertLessEqual(largest_ordinary_body, dp6.TEMP_MODEL_CAPACITY)
        self.assertEqual(dp6.TEMP_MODEL_CAPACITY - largest_ordinary_body, 0x928)

    def test_all_scanned_scenarios_and_formations_fit_dp6(self) -> None:
        if not LAYOUT_REPORT.is_file():
            self.skipTest("Full layout-ranking report absent")

        archive_ids = {
            "HICLOUD.LZS": 0,
            "BARRETT.LZS": 1,
            "TIFA.LZS": 2,
            "EARITH.LZS": 3,
            "RED13.LZS": 4,
            "YUFI.LZS": 5,
            "KETCY.LZS": 6,
            "CID.LZS": 8,
            "VINSENT3.LZS": 15,
        }
        arena_capacity = dp6.ACTOR_ARENA_LIMIT - dp6.ACTOR_ARENA_BASE
        scenarios: set[str] = set()
        formations_by_scenario: dict[str, set[int]] = {}
        row_count = 0
        minimum_arena_margin = arena_capacity
        minimum_split_margin = dp6.TEMP_MODEL_CAPACITY
        maximum_dp2_overflow = 0

        with LAYOUT_REPORT.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                scenario = row["scenario"]
                formation_id = int(row["formation_id"])
                model_ids = sorted(
                    archive_ids[name] for name in row["party_archives"].split()
                )
                pairs = [(model_id, index) for index, model_id in enumerate(model_ids)]
                pairs.extend(
                    (0xC8, index) for index in range(len(pairs), 3)
                )
                pointers, enemy_start, restore_needed = plan_layout(pairs)
                enemy_end = enemy_start + int(row["enemy_bytes"])
                self.assertLessEqual(
                    enemy_end,
                    dp6.ACTOR_ARENA_LIMIT,
                    f"{scenario}, formation {formation_id}",
                )
                minimum_arena_margin = min(
                    minimum_arena_margin,
                    dp6.ACTOR_ARENA_LIMIT - enemy_end,
                )

                if len(model_ids) == 3:
                    split_id = model_ids[2]
                    split_size = PLAYER_BODY_SIZES[split_id]
                    split_end = dp6.TEMP_MODEL_BASE + split_size
                    self.assertTrue(restore_needed)
                    self.assertEqual(pointers[2], dp6.TEMP_MODEL_BASE)
                    self.assertLessEqual(
                        split_end,
                        dp6.TEMP_MODEL_LIMIT,
                    )
                    minimum_split_margin = min(
                        minimum_split_margin,
                        dp6.TEMP_MODEL_LIMIT - split_end,
                    )
                else:
                    self.assertFalse(restore_needed)

                maximum_dp2_overflow = max(
                    maximum_dp2_overflow,
                    max(
                        0,
                        int(row["party_bytes"])
                        + int(row["enemy_bytes"])
                        - arena_capacity,
                    ),
                )
                scenarios.add(scenario)
                formations_by_scenario.setdefault(scenario, set()).add(formation_id)
                row_count += 1

        self.assertEqual(len(scenarios), 37)
        self.assertEqual(row_count, 37 * 1024)
        self.assertTrue(
            all(len(formations) == 1024 for formations in formations_by_scenario.values())
        )
        self.assertEqual(maximum_dp2_overflow, 0x5D84)
        self.assertEqual(minimum_arena_margin, 0x9030)
        self.assertEqual(minimum_split_margin, 0x928)

    def test_zolom_red_vincent_layout_now_fits(self) -> None:
        pointers, enemy_start, restore_needed = plan_layout(
            [(0, 0), (4, 1), (15, 2)]
        )
        self.assertEqual(
            pointers,
            (0x80103200, 0x801199F4, dp6.TEMP_MODEL_BASE),
        )
        self.assertTrue(restore_needed)
        self.assertEqual(enemy_start, 0x801288CC)
        zolom_end = enemy_start + 0x1B268
        self.assertEqual(zolom_end, 0x80143B34)
        self.assertEqual(dp6.ACTOR_ARENA_LIMIT - zolom_end, 0xD6CC)
        self.assertEqual(
            dp6.TEMP_MODEL_LIMIT
            - (dp6.TEMP_MODEL_BASE + PLAYER_BODY_SIZES[15]),
            0xA4C,
        )

    def test_body_sizes_match_full_disc_inventory(self) -> None:
        if not MODEL_REPORT.is_file():
            self.skipTest("Full-disc model report absent")
        by_name: dict[str, int] = {}
        with MODEL_REPORT.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                if row["body_prefix_aligned4"]:
                    by_name[row["stem"]] = int(row["body_prefix_aligned4"])

        expected = {
            "CLOUD": 0xB408,
            "HICLOUD": 0x167F4,
            "BARRETT": 0xBE30,
            "TIFA": 0xDB54,
            "EARITH": 0xDE3C,
            "RED13": 0xEED8,
            "YUFI": 0xE6C4,
            "KETCY": 0xEAD4,
            "VINSENT": 0xEB88,
            "CID": 0xD19C,
            "SEFIROS": 0xC8DC,
            "BARRETT2": 0xB8E8,
            "BARRETT3": 0xB714,
            "BARRETT4": 0xB77C,
            "VINSENT2": 0xEC9C,
            "VINSENT3": 0xEDB4,
        }
        for name, size in expected.items():
            self.assertEqual(by_name[name], size, name)

    def test_battle_patch_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "BATTLE.X"
            digest, decompressed_digest = dp6.patch_file(
                SOURCE_BATTLE,
                output,
                REFERENCE_RESTORE_LBA,
            )
            self.assertEqual(
                digest,
                "b5079b8e5143e708ac7ff7c1931f18739ee259e04e7acae2ccf95ba25bb9db1a",
            )
            self.assertEqual(
                decompressed_digest,
                "4d554faf92e044637727f5fe99498a9e082c07e2",
            )
            self.assertEqual(len(output.read_bytes()), len(SOURCE_BATTLE.read_bytes()))

    def test_equivalent_gzip_stream_is_accepted(self) -> None:
        clean = SOURCE_BATTLE.read_bytes()
        equivalent = bytearray(clean)
        equivalent[12:16] = b"\x78\x56\x34\x12"
        self.assertNotEqual(
            hashlib.sha256(equivalent).hexdigest(),
            dp6.REFERENCE_PACKED_SHA256,
        )
        self.assertEqual(
            hashlib.sha1(gzip.decompress(equivalent[8:])).hexdigest(),
            dp6.EXPECTED_DECOMPRESSED_SHA1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "BATTLE.X"
            digest, decompressed_digest = dp6.patch_file_from_bytes(
                bytes(equivalent),
                output,
                REFERENCE_RESTORE_LBA,
            )
            self.assertEqual(
                digest,
                "b5079b8e5143e708ac7ff7c1931f18739ee259e04e7acae2ccf95ba25bb9db1a",
            )
            self.assertEqual(
                decompressed_digest,
                hashlib.sha1(gzip.decompress(output.read_bytes()[8:])).hexdigest(),
            )

    def test_palette64_patch(self) -> None:
        encoded = (PACKAGE / dp6.PALETTE_PATCH_NAME).read_text(encoding="ascii")
        patched = dp6.apply_bps(
            SOURCE_HICLOUD.read_bytes(),
            base64.b64decode(encoded),
        )
        self.assertEqual(len(patched), dp6.HICLOUD_SIZE)
        self.assertEqual(
            hashlib.sha256(patched).hexdigest(),
            dp6.PATCHED_HICLOUD_SHA256,
        )

    def test_direct_disc_patch(self) -> None:
        clean_battle = SOURCE_BATTLE.read_bytes()
        synthetic_main = build_synthetic_main()
        synthetic_restore_hash = hashlib.sha256(
            synthetic_main[
                dp6.MAIN_RESTORE_FILE_OFFSET :
                dp6.MAIN_RESTORE_FILE_OFFSET + dp6.MAIN_RESTORE_SIZE
            ]
        ).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            clean_bin, clean_cue = build_test_disc(
                temp,
                clean_battle,
                SOURCE_HICLOUD.read_bytes(),
                synthetic_main,
            )
            original_bin_hash = hashlib.sha256(clean_bin.read_bytes()).hexdigest()
            with mock.patch.object(
                dp6,
                "EXPECTED_MAIN_RESTORE_WINDOW_SHA256",
                synthetic_restore_hash,
            ):
                output_bin, output_cue, embedded_hash, restore_lba = dp6.patch_disc(
                    clean_cue
                )

            self.assertEqual(
                hashlib.sha256(clean_bin.read_bytes()).hexdigest(),
                original_bin_hash,
            )
            self.assertEqual(
                restore_lba,
                SYNTHETIC_MAIN_LBA + dp6.MAIN_RESTORE_SECTOR_OFFSET,
            )
            self.assertIn(output_bin.name, output_cue.read_text(encoding="utf-8"))

            track, _ = dp6.parse_single_bin_mode2_cue(output_cue)
            with dp6.Mode2SectorReader(track.file_path, track.index01_frames) as reader:
                iso = dp6.Iso9660(reader)
                entry = iso.files["BATTLE/BATTLE.X"]
                embedded = reader.read_extent(entry.lba, entry.size)
                embedded_hicloud = reader.read_extent(
                    dp6.HICLOUD_LBA,
                    dp6.HICLOUD_SIZE,
                )
                main_entry = iso.files["SCUS_941.65"]
                embedded_main = reader.read_extent(main_entry.lba, main_entry.size)
            self.assertEqual(hashlib.sha256(embedded).hexdigest(), embedded_hash)
            decompressed = gzip.decompress(embedded[8:])
            wrapper_offset = 0x800D2498 - dp6.LOAD_ADDRESS
            wrapper = struct.unpack_from("<20I", decompressed, wrapper_offset)
            injected_lba = ((wrapper[9] & 0xFFFF) << 16) | (
                wrapper[10] & 0xFFFF
            )
            self.assertEqual(injected_lba, restore_lba)
            self.assertEqual(
                hashlib.sha256(embedded_hicloud).hexdigest(),
                dp6.PATCHED_HICLOUD_SHA256,
            )
            self.assertEqual(embedded_main, synthetic_main)

            with output_bin.open("rb") as image:
                image.seek(entry.lba * 2352)
                sector = image.read(2352)
            regenerated = bytearray(sector)
            dp6.regenerate_mode2_form1_checksums(regenerated)
            self.assertEqual(bytes(regenerated), sector)


class PackagedArtifactTests(unittest.TestCase):
    def test_prepatched_battle_is_intentionally_not_shipped(self) -> None:
        # DP6 embeds a restore LBA derived from the exact clean disc. A fixed
        # prepatched BATTLE.X would silently restore from the wrong sectors.
        self.assertFalse((PACKAGE / "BATTLE_HICLOUD_DP6.X").exists())

    def test_package_entrypoints_are_dp6(self) -> None:
        required = (
            "Patch_FF7_Disc.bat",
            "patch_hicloud_dp6.py",
            "README.txt",
            "VALIDATION.txt",
            "Run_Self_Tests.bat",
            "test_dp6.py",
            "_hicloud_bps.py",
            dp6.PALETTE_PATCH_NAME,
        )
        for name in required:
            self.assertTrue((PACKAGE / name).is_file(), name)
        batch = (PACKAGE / "Patch_FF7_Disc.bat").read_text(encoding="utf-8")
        self.assertIn("patch_hicloud_dp6.py", batch)
        self.assertNotIn("patch_hicloud_dp2.py", batch)


if __name__ == "__main__":
    unittest.main(verbosity=2)

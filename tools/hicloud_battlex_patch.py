#!/usr/bin/env python3
"""Build the position-aware Project HiCloud BATTLE.X prototype for FF7 US discs.

The input must be the unmodified BATTLE.X shared by SCUS-94163/94164/94165.
The output keeps the original eight-byte wrapper, patches the decompressed
overlay, and recompresses it as deterministic gzip data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
from pathlib import Path


VRAM_BASE = 0x800A0000
EXPECTED_INPUT_SHA256 = (
    "0ac8e4297dd83226e18ebeffc20d1c372fd8ca7838c1edb7ec48138858f08e88"
)
EXPECTED_DEC_SHA1 = "c82690f814b18664c3b2d024f4edeea5743995d9"
EXPECTED_DEC_SIZE = 0x538AC
ORIGINAL_ALLOCATION = 64 * 2048

# The original final-battle renderer selects HiCloud's 8-bit palette with a
# hard-coded GetClut(320, 195) value.  The diagnostic CLUT revision uploads the
# physical-slot-1 palette at (0, 251), whose encoded GPU value is 0x3EC0.
CLUT_REFERENCE_PATCHES = [
    (0x800B4458, 0x340230D4, 0x34023EC0, "HiCloud draw CLUT -> (0,251)"),
    (0x800B9CE4, 0x340230D4, 0x34023EC0, "HiCloud fallback CLUT -> (0,251)"),
]

CLUT_4BPP_PATCHES = [
    (0x800B4458, 0x340230D4, 0x340278C0, "4-bit HiCloud draw CLUT -> (0,483)"),
    (0x800B9CE4, 0x340230D4, 0x340278C0, "4-bit HiCloud fallback CLUT -> (0,483)"),
]


# (runtime address, expected original word, replacement word, description)
BASE_PATCHES = [
    # Point Cloud's Yamada entry to the native HiCloud archive.
    (0x800E8068, 0x00007573, 0x000077B5, "Cloud archive LBA -> HiCloud"),
    (0x800E806C, 0x00010800, 0x00018800, "Cloud archive read length -> HiCloud"),

    # The player arena grows by HiCloud's measured 0x77F4 overflow.  Move the
    # enemy-model arena start by the same amount before any enemy body copy.
    (0x800B3A1C, 0x24420200, 0x244279F4, "enemy arena base 80130200 -> 801379F4"),

    # This exported overlay datum is the only embedded absolute pointer into
    # the original enemy-model arena. Keep its +0x2380 relative position after
    # shifting the arena by 0x77F4.
    (0x800EE318, 0x80132580, 0x80139D74, "shift embedded enemy-arena pointer"),

    # The loader always schedules Cloud first, while v1 is the physical party
    # index. Allocate the first body at the first sequential base regardless of
    # physical index; keep a0 = v1 * 4 so the pointer is still stored under the
    # correct physical party-table entry.
    (0x800B3AD8, 0x00031100, 0x3C028010, "first body base high half"),
    (0x800B3ADC, 0x00431023, 0x34423200, "first body base low half"),
    (0x800B3AE0, 0x00021300, 0x00000000, "remove physical-index stride"),
    (0x800B3AE4, 0x3C038010, 0x00000000, "remove fixed base calculation"),
    (0x800B3AE8, 0x24633200, 0x00000000, "remove fixed base calculation"),
    (0x800B3AEC, 0x00431021, 0x00000000, "first body base already selected"),

    # Second model callback: start at the vanilla second reservation. If the
    # first loaded model ID (D_800FA9C4) is Cloud/HiCloud ID 0, add the measured
    # 0x77F4 expansion. This makes the allocation follow Cloud's position.
    (0x800B3BA4, 0x00031100, 0x3C028011, "slot 2 base high half"),
    (0x800B3BA8, 0x00431023, 0x3C018010, "first model ID address high half"),
    (0x800B3BAC, 0x00021300, 0x8421A9C4, "load first model ID"),
    (0x800B3BB0, 0x3C038010, 0x34422200, "vanilla second-model base and load delay"),
    (0x800B3BB4, 0x24633200, 0x14200002, "skip expansion unless first model is Cloud"),
    (0x800B3BB8, 0x00431021, 0x00000000, "safe branch delay slot"),
    (0x800B3BBC, 0x3C018010, 0x244277F4, "add HiCloud expansion after Cloud"),
    (0x800B3BC0, 0x24218384, 0x3C018010, "compressed pointer-store address"),
    (0x800B3BC8, 0xAC220000, 0xAC228384, "store through compressed pointer address"),

    # Third model callback: use the shifted third reservation unless the current
    # model ID (D_800FA9CC) is Cloud/HiCloud ID 0. If Cloud is current, remove
    # the expansion because no earlier model needs it.
    (0x800B3C70, 0x00031100, 0x3C028012, "slot 3 base high half"),
    (0x800B3C74, 0x00431023, 0x3C018010, "current model ID address high half"),
    (0x800B3C78, 0x00021300, 0x8421A9CC, "load current model ID"),
    (0x800B3C7C, 0x3C038010, 0x344289F4, "shifted third-model base and load delay"),
    (0x800B3C80, 0x24633200, 0x14200002, "keep expansion unless current model is Cloud"),
    (0x800B3C84, 0x00431021, 0x00000000, "safe branch delay slot"),
    (0x800B3C88, 0x3C018010, 0x2442880C, "remove expansion when current is Cloud"),
    (0x800B3C8C, 0x24218384, 0x3C018010, "compressed pointer-store address"),
    (0x800B3C94, 0xAC220000, 0xAC228384, "store through compressed pointer address"),

    # Body copies must consume the pointer table rather than recalculate
    # 0x80103200 + slot * 0xF000.
    (0x800B5C30, 0x00102100, 0x00102080, "pointer-table index = slot * 4"),
    (0x800B5C34, 0x00902023, 0x3C038010, "pointer-table address high half"),
    (0x800B5C38, 0x00042300, 0x24638384, "pointer-table address low half"),
    (0x800B5C3C, 0x3C038010, 0x00641821, "pointer-table entry address"),
    (0x800B5C40, 0x24633200, 0x8C640000, "load selected body destination"),
    (0x800B5C4C, 0x00832021, 0x00000000, "destination already loaded"),

    # The mid-battle model reload path must preserve the established pointer
    # instead of restoring the vanilla fixed-stride address.
    (0x800CCAE4, 0x00101100, 0x00000000, "preserve dynamic body pointer"),
    (0x800CCAE8, 0x00501023, 0x00000000, "preserve dynamic body pointer"),
    (0x800CCAEC, 0x00021300, 0x00000000, "preserve dynamic body pointer"),
    (0x800CCAF0, 0x00431021, 0x00000000, "preserve dynamic body pointer"),
    (0x800CCAF8, 0xAE220000, 0x00000000, "do not overwrite pointer-table entry"),
]


def patches_for(
    enemy_arena: int, relocate_clut: bool = False, clut_4bpp: bool = False
) -> list[tuple[int, int, int, str]]:
    """Return the position-aware patch set for the selected enemy arena.

    The normal build retains the tested 2 MiB layout at 0x801379F4.  The
    experimental SS1 build moves the enemy-model arena into expansion RAM and
    adjusts the one exported absolute pointer that follows that arena.
    """
    if enemy_arena == 0x801379F4:
        patches = list(BASE_PATCHES)
        if relocate_clut and clut_4bpp:
            raise SystemExit("error: select only one CLUT diagnostic")
        if relocate_clut:
            patches.extend(CLUT_REFERENCE_PATCHES)
        if clut_4bpp:
            patches.extend(CLUT_4BPP_PATCHES)
        return sorted(patches, key=lambda item: item[0])
    if enemy_arena != 0x80200000:
        raise SystemExit("error: unsupported enemy arena")

    replacements = {
        0x800B3A18: (0x3C028013, 0x3C028020, "enemy arena base high half -> SS1 expansion RAM"),
        0x800B3A1C: (0x24420200, 0x24420000, "enemy arena base -> 0x80200000"),
        0x800EE318: (0x80132580, 0x80202380, "move embedded enemy-arena pointer to expansion RAM"),
    }
    patches = []
    seen = set()
    for address, expected, replacement, description in BASE_PATCHES:
        if address in replacements:
            patches.append((address, *replacements[address]))
            seen.add(address)
        else:
            patches.append((address, expected, replacement, description))
    # The normal patch changes only the low half at 0x800B3A1C, so the SS1
    # build additionally changes the preceding LUI.
    if 0x800B3A18 not in seen:
        patches.append((0x800B3A18, *replacements[0x800B3A18]))
    if relocate_clut and clut_4bpp:
        raise SystemExit("error: select only one CLUT diagnostic")
    if relocate_clut:
        patches.extend(CLUT_REFERENCE_PATCHES)
    if clut_4bpp:
        patches.extend(CLUT_4BPP_PATCHES)
    return sorted(patches, key=lambda item: item[0])


def digest(data: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, data).hexdigest()


def word_at(data: bytes | bytearray, address: int) -> int:
    return struct.unpack_from("<I", data, address - VRAM_BASE)[0]


def put_word(data: bytearray, address: int, value: int) -> None:
    struct.pack_into("<I", data, address - VRAM_BASE, value)


def build(
    source: Path,
    output: Path,
    enemy_arena: int = 0x801379F4,
    relocate_clut: bool = False,
    clut_4bpp: bool = False,
) -> dict[str, object]:
    wrapped = source.read_bytes()
    source_sha256 = digest(wrapped, "sha256")
    if source_sha256 != EXPECTED_INPUT_SHA256:
        raise SystemExit(
            "error: input is not the verified US BATTLE.X\n"
            f"expected SHA-256 {EXPECTED_INPUT_SHA256}\n"
            f"actual   SHA-256 {source_sha256}"
        )
    if len(wrapped) < 18 or wrapped[8:10] != b"\x1f\x8b":
        raise SystemExit("error: BATTLE.X wrapper does not contain gzip data at +8")

    header = wrapped[:8]
    declared_size = struct.unpack_from("<I", header, 0)[0]
    dec = bytearray(gzip.decompress(wrapped[8:]))
    if declared_size != EXPECTED_DEC_SIZE or len(dec) != EXPECTED_DEC_SIZE:
        raise SystemExit("error: unexpected decompressed BATTLE.X size")
    dec_sha1 = digest(dec, "sha1")
    if dec_sha1 != EXPECTED_DEC_SHA1:
        raise SystemExit(
            "error: decompressed overlay does not match the verified US build\n"
            f"expected SHA-1 {EXPECTED_DEC_SHA1}\nactual   SHA-1 {dec_sha1}"
        )

    applied = []
    for address, expected, replacement, description in patches_for(
        enemy_arena, relocate_clut=relocate_clut, clut_4bpp=clut_4bpp
    ):
        actual = word_at(dec, address)
        if actual != expected:
            raise SystemExit(
                f"error: precondition failed at 0x{address:08X}: "
                f"expected {expected:08X}, found {actual:08X}"
            )
        put_word(dec, address, replacement)
        applied.append(
            {
                "address": f"0x{address:08X}",
                "before": f"0x{expected:08X}",
                "after": f"0x{replacement:08X}",
                "description": description,
            }
        )

    # A round-trip check protects against a malformed wrapper or compressor.
    compressed = gzip.compress(bytes(dec), compresslevel=9, mtime=0)
    result = header + compressed
    if len(result) > len(wrapped):
        raise SystemExit(
            f"error: patched compressed stream is {len(result)} bytes, exceeding "
            f"the original {len(wrapped)}-byte file"
        )
    # Preserve the ISO directory size exactly. The game's decompressor stops at
    # the gzip end marker; zero fill is inert and avoids filesystem rewrites.
    result += bytes(len(wrapped) - len(result))
    if gzip.decompress(result[8:]) != bytes(dec):
        raise SystemExit("error: internal gzip round-trip validation failed")
    if len(result) > ORIGINAL_ALLOCATION:
        raise SystemExit(
            f"error: patched BATTLE.X is {len(result)} bytes, exceeding the "
            f"original {ORIGINAL_ALLOCATION}-byte ISO allocation"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return {
        "input": str(source),
        "output": str(output),
        "input_size": len(wrapped),
        "output_size": len(result),
        "iso_allocation": ORIGINAL_ALLOCATION,
        "input_sha256": source_sha256,
        "output_sha256": digest(result, "sha256"),
        "decompressed_output_sha1": digest(dec, "sha1"),
        "decompressed_size": len(dec),
        "enemy_arena": f"0x{enemy_arena:08X}",
        "patch_count": len(applied),
        "patches": applied,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="verified original US BATTLE.X")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument(
        "--ss1-8mb",
        action="store_true",
        help="experimental: move the enemy-model arena to 0x80200000",
    )
    parser.add_argument(
        "--relocate-clut",
        action="store_true",
        help="diagnostic: select the relocated HiCloud palette at (0,251)",
    )
    parser.add_argument(
        "--clut-4bpp",
        action="store_true",
        help="diagnostic: select the 4-bit HiCloud palette at (0,483)",
    )
    args = parser.parse_args()
    arena = 0x80200000 if args.ss1_8mb else 0x801379F4
    report = build(
        args.input,
        args.output,
        enemy_arena=arena,
        relocate_clut=args.relocate_clut,
        clut_4bpp=args.clut_4bpp,
    )
    print(f"wrote: {report['output']}")
    print(f"size:  {report['output_size']} / {report['iso_allocation']} bytes")
    print(f"sha256: {report['output_sha256']}")
    print(f"patches: {report['patch_count']}")
    print(f"enemy arena: {report['enemy_arena']}")


if __name__ == "__main__":
    main()

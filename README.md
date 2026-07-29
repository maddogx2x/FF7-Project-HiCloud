# Project HiCloud

Project HiCloud makes Final Fantasy VII's native high-detail Cloud battle model
usable throughout the NTSC-U PlayStation game while preserving the retail
battle renderer, all three party slots, weapon behavior, and standard **2 MiB**
PlayStation RAM.

## Golden 2 MiB release

DP6 + Palette64 is the first hardware-validated 2 MiB build. It solves both
independent resource problems:

- dynamic battle-model packing prevents HiCloud's oversized body from
  overwriting another actor;
- reversible split storage gives the heaviest formations enough battle RAM
  without changing the retail renderer or requiring extended memory;
- Palette64 compacts HiCloud's 8-bpp palette from 198 used colors to 64 entries,
  preventing its CLUT upload from overwriting populated battle-background
  palettes.

The worst-case tested party—HiCloud, Red XIII, and Vincent—passed regular
battles, Midgar Zolom, Jenova-LIFE, and the Northern Crater through the final
Sephiroth encounters. Testing included Limits, Knights of the Round, other
summons, varied magic, results/field return, and the final one-on-one battle
that transitions directly into an FMV.

## Requirements

- Final Fantasy VII NTSC-U, disc serial `SCUS-94163`, `SCUS-94164`, or
  `SCUS-94165`
- a single-BIN MODE2/2352 image with its CUE file
- Python 3.10 or newer
- standard 2 MiB PlayStation RAM

DP6 does not require an 8 MiB emulator or FPGA mode.

## Applying DP6

1. Download or clone this repository.
2. Open `releases/DP6_2MiB_Palette64`.
3. Keep the original NTSC-U BIN and CUE together.
4. On Windows, drag the clean CUE onto `Patch_FF7_Disc.bat`.
5. Boot the generated `Final Fantasy VII (Disc N)_HighRes_Cloud.cue`.

The patcher determines the disc from the verified executable serial and outputs
the matching Disc 1, Disc 2, or Disc 3 BIN/CUE name automatically.

The patcher:

- identifies all three NTSC-U discs through ISO9660;
- verifies clean retail `BATTLE.X`, `HICLOUD.LZS`, and the executable restore
  window;
- derives the disc-specific executable LBA instead of embedding an unsafe fixed
  address;
- copies the source image and never patches it in place;
- rebuilds Mode 2 Form 1 EDC/ECC;
- reads the completed image back and verifies every patched resource.

Only patch logic and a size-preserving Palette64 BPS delta are distributed. No
complete copyrighted game file or prepatched `BATTLE.X` is included.

## How 2 MiB was solved

Retail battle loading reserves three fixed `0xF000`-byte player slots beginning
at `0x80103200`. HiCloud reaches `0x167F4` bytes, so it can exceed a normal slot
and collide with the next player. DP2 proved that model loading could instead
use a dynamic pointer table and size-aware packing, but the largest
party/formation combinations could still exceed the remaining battle arena by
`0x5D84` bytes.

Broad CPU/DMA tracing and synchronized RAM snapshots—including a complete
Knights of the Round sequence—identified
`0x80052800–0x80062000` (`0xF800` bytes) as FFVII's static MDEC/VLC decode
table. Battles do not use this table, but later FMVs do, so DP6 treats it as
temporary borrowed storage rather than free RAM:

1. HiCloud and one ordinary teammate remain in the dynamically packed actor
   arena.
2. The third sorted ordinary model is placed in the `0xF800` MDEC/VLC window.
3. Enemies follow the two arena-resident party models.
4. Results run normally while actor pointers are still valid.
5. Before field control resumes, DP6 uses FFVII's synchronous raw-sector reader
   to restore the exact original 31 executable sectors.

Red XIII is the largest ordinary party body at `0xEED8`, leaving `0x928` bytes
in temporary storage. All 37,888 scanned HiCloud party/formation layouts fit;
the minimum remaining actor-arena margin is `0x9030`.

The restoration detail matters. DP5 accidentally called FFVII's streaming
decompression reader, which expanded raw executable data past the borrowed
window and corrupted timer globals during results. DP6 changes that call to the
synchronous raw reader. The final battle's successful direct transition into
an FMV provides hardware evidence that restoration completes before MDEC is
needed again.

## Hardware validation

| Scenario | Result |
|---|---|
| HiCloud + Red XIII + Vincent, regular battle | Passed |
| Midgar Zolom | Passed |
| Jenova-LIFE | Passed |
| Northern Crater through final battles | Passed |
| Limits, Knights of the Round, summons, and magic | Passed |
| Results and return to field | Passed |
| Final Cloud vs. Sephiroth → immediate FMV | Passed |

## Legacy 8 MiB build

The original 8 MiB/Palette64 patcher remains at the repository root for
historical comparison and extended-memory testing. DP6 is the recommended build
for standard hardware.

## Documentation

- [Technical implementation](docs/TECHNICAL_IMPLEMENTATION.md)
- [How the 2 MiB solution works](docs/WHY_2MB_IS_UNSOLVED.md)
- [VRAM and Palette64](docs/VRAM_PALETTE64.md)
- [Test matrix](docs/TEST_MATRIX.md)
- [Research history](docs/RESEARCH_HISTORY.md)
- [DP6 validation record](releases/DP6_2MiB_Palette64/VALIDATION.txt)
- [Contributing](CONTRIBUTING.md)

## Project status

DP6 + Palette64 is the golden hardware-validated 2 MiB release. Additional
testing and reports remain welcome; include the disc, formation, party order,
platform/core version, and whether any issue occurs during load, battle,
results, restoration, field return, or FMV playback.

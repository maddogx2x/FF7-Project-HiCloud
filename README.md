# Project HiCloud

Project HiCloud makes Final Fantasy VII's high-detail battle Cloud model usable
throughout the NTSC-U PlayStation game while preserving the rest of each
equipped weapon's behavior and the original battle system.

The current release candidate is the first configuration we have tested that
solves both known failure classes:

- an 8 MiB PlayStation RAM mode prevents large battle formations—most notably
  Midgar Zolom—from colliding with the oversized player model;
- HiCloud's 8-bpp palette is compacted from 198 used colors to 64 entries, so
  its CLUT upload no longer overwrites populated battle-background palettes.

In testing, Midgar Zolom loads and the battle completes, Cloud's eyes and belt
render correctly, the skybox strip is gone, and several large late-game battles
also load normally.

## Requirements

- Final Fantasy VII NTSC-U, disc serial `SCUS-94163`, `SCUS-94164`, or
  `SCUS-94165`, as a single-BIN MODE2/2352 image with its CUE file
- Python 3.10 or newer
- a platform with 8 MiB PSX RAM support, tested with SuperStation One and
  DuckStation
- 8 MiB RAM enabled before booting the patched image

Standard 2 MiB PlayStation hardware is **not currently supported**. This is an
experimental extended-memory mod, not a retail-hardware-compatible release.

## Applying the release

1. Download this repository or a release archive.
2. Keep the original NTSC-U `.bin` and `.cue` together.
3. On Windows, drag the CUE onto `Patch_HiCloud_8MB_Palette64_Windows.bat`.
4. Or run:

   ```text
   python Patch_HiCloud.py "Final Fantasy VII (Disc 1).cue"
   ```

5. Enable 8 MiB PSX RAM and boot the newly generated
   `*_HiCloud_8MB_Palette64.cue`.

The patcher validates the disc identity and source hashes, never edits the
original image, rebuilds Mode 2 Form 1 EDC/ECC, and reads the result back for
verification.

## What changed

The project changes two disc-resident resources:

- `BATTLE.X`: redirects ordinary Cloud battle loads to the native high-detail
  archive and uses the tested positional player-layout patch;
- `HICLOUD.LZS`: retains the model, UVs, texture dimensions, 8-bpp mode, TIM
  layout, and CLUT origin while remapping its texture indices to a 64-entry
  palette.

Only BPS deltas are distributed. No complete game file is included.

## Why 2 MiB still fails

The normal player-body allocation has a `0xF000`-byte slot stride. HiCloud's
body reaches `0x167F4` bytes, overflowing a normal slot by `0x77F4` bytes. A
following player can overwrite that tail. Repacking the player models fixes
simple formations, but heavy enemy formations then push the enlarged player
arena into other live battle allocations. Regions that looked unused in single
RAM snapshots proved to be BSS, heap, staging, or later-loaded data.

More detail and concrete addresses are in
[`docs/WHY_2MB_IS_UNSOLVED.md`](docs/WHY_2MB_IS_UNSOLVED.md). Contributions that
produce a formation-safe 2 MiB allocator or a smaller compatible model layout
are especially welcome.

## Documentation

- [Technical implementation](docs/TECHNICAL_IMPLEMENTATION.md)
- [Why 2 MiB is unsolved](docs/WHY_2MB_IS_UNSOLVED.md)
- [VRAM and Palette64](docs/VRAM_PALETTE64.md)
- [Test matrix](docs/TEST_MATRIX.md)
- [Research history](docs/RESEARCH_HISTORY.md)
- [Contributing](CONTRIBUTING.md)

## Project status

The 8 MiB/Palette64 build is a working release candidate, not a claim of full
game certification. Keep an original image and report the disc, formation,
party order, platform/core version, and whether the failure occurs during load,
battle, or cleanup.


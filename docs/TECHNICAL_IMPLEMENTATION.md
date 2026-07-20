# Technical implementation

## Disc scope

The patcher accepts the three NTSC-U discs (`SCUS-94163`, `SCUS-94164`, and
`SCUS-94165`) because their verified `BATTLE.X` is identical. It requires a
single-BIN MODE2/2352 image so raw-sector offsets and EDC/ECC can be rebuilt
deterministically.

## Inputs and verified hashes

| Source | Size | SHA-256 |
|---|---:|---|
| Original NTSC-U `BATTLE.X` | 130,322 | `0ac8e4297dd83226e18ebeffc20d1c372fd8ca7838c1edb7ec48138858f08e88` |
| Original native `HICLOUD.LZS` | 99,118 | `3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4` |

The native HiCloud archive is addressed through the game's YAMADA table at raw
LBA `0x77B5`; it is not an ordinary ISO9660 directory entry. Its reserved disc
allocation is `0x18800` bytes.

## Build pipeline

1. Read and validate the ISO9660 PVD, `/BATTLE/BATTLE.X`, `SYSTEM.CNF`, serial,
   and raw native HiCloud allocation.
2. Apply BPS1 deltas to the verified source resources in memory.
3. Refuse unexpected hashes, sizes, disc layouts, or output collisions.
4. Copy the source image; never patch it in place.
5. Inject the changed user-data sectors and rebuild Mode 2 Form 1 EDC/ECC.
6. Re-read the output through the same ISO/raw-LBA paths and compare every
   patched byte before publishing the output CUE/BIN pair.

## Runtime requirement

The patch does not turn FF7 into a general 8 MiB-aware application. It relies
on an emulator/FPGA core that maps the extended PSX RAM address range in a way
compatible with the modified battle model loading. Enable 8 MiB before boot.


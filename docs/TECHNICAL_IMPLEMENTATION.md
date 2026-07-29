# Technical implementation

## Disc scope

The DP6 patcher accepts the three NTSC-U discs (`SCUS-94163`, `SCUS-94164`,
and `SCUS-94165`) as single-BIN MODE2/2352 images. Their verified `BATTLE.X`
is identical, while the main executable LBA used for restoration is located
and injected separately for each disc image.

## Verified inputs

| Source | Size | SHA-256 |
|---|---:|---|
| Retail NTSC-U `BATTLE.X` | 130,322 | `0ac8e4297dd83226e18ebeffc20d1c372fd8ca7838c1edb7ec48138858f08e88` |
| Native `HICLOUD.LZS` | 99,118 | `3fa1293e7aa4bca5c6d5f5957e558a1e493a9cf70163d448a3dbc4e5c8fbe6f4` |
| MDEC/VLC restore window | 63,488 | `c3b1d2b3a49b928a176210713ac8334e5a6b45eec2c774305dbc0e9084fa0a44` |
| Palette64 `HICLOUD.LZS` | 99,118 | `72b2121727159403eb4e36bc7e8ff670f1230a6c84a90cb5060b1efbb6967746` |

The native HiCloud archive is addressed through the game's YAMADA table at raw
LBA `0x77B5`; it is not an ordinary ISO9660 directory entry.

## Runtime layout

- Actor arena: `0x80103200–0x80151200`
- Temporary ordinary-model window: `0x80052800–0x80062000`
- Temporary capacity: `0xF800`
- Restore size: 31 × 2,048-byte sectors
- Raw synchronous reader: `func_80033F40`
- Rejected streaming decompression reader: `func_80033FC4`

DP6 completes the dynamic party layout before enemy decompression. In a full
HiCloud party, HiCloud and one teammate remain in the actor arena, while the
third sorted ordinary model uses the temporary window. Enemies then occupy the
remaining actor arena. All later player-address reconstruction uses the dynamic
pointer table.

Results run before restoration so no live actor pointer is invalidated. The
restore wrapper then blocks field return while the raw reader copies the clean
MDEC/VLC sectors back into RAM.

## Disc build pipeline

1. Parse and validate the CUE, ISO9660 PVD, `/BATTLE/BATTLE.X`, main executable,
   serial, and native HiCloud allocation.
2. Verify the main executable's PS-X EXE header, load address, and complete
   restore-window hash.
3. Derive restore LBA as executable LBA + `0x86`.
4. Build DP6 `BATTLE.X` with that disc-specific LBA.
5. Apply the verified Palette64 BPS delta in memory.
6. Copy the source image; never patch it in place.
7. Inject changed user-data sectors and rebuild Mode 2 Form 1 EDC/ECC.
8. Re-read the output and verify `BATTLE.X`, `HICLOUD.LZS`, disc identity, and
   restore parameters.

No prepatched fixed-LBA `BATTLE.X` is shipped.

## Validation

The package includes 23 automated regressions, an exhaustive 37,888-layout
scan, R3000A load-delay checks, renderer identity checks, DP5 crash forensics,
memory-evidence checks, and synthetic direct-disc patch/read-back coverage.
See [`../releases/DP6_2MiB_Palette64/VALIDATION.txt`](../releases/DP6_2MiB_Palette64/VALIDATION.txt).

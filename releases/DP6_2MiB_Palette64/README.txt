FINAL FANTASY VII NTSC-U — HICLOUD DYNAMIC PACKING DP6 + PALETTE64
=================================================================

STATUS
------
HARDWARE-VALIDATED GOLDEN 2 MiB BUILD.

DP6 corrects DP5's results-teardown restore call. It retains the
hardware-proven DP2 allocator, DP5's successful split-storage layout, and the
retail GPU renderer. Always patch a clean NTSC-U BIN/CUE. Do not stack DP6 on
any earlier build.

WHAT DP6 CHANGES
----------------
DP2 proved the correct battle-load ordering, but its single contiguous actor
arena can still exceed the battle-state boundary by as much as 0x5D84 bytes.

For a full three-member HiCloud party, DP6:

  1. Keeps HiCloud and one teammate in DP2's normal actor arena.
  2. Places the third sorted ordinary party model at:

       0x80052800-0x80062000  (0xF800 bytes)

  3. Loads enemies immediately after the two arena-resident party models.
  4. Leaves the retail double-buffered GPU renderer byte-for-byte unchanged.
  5. Runs BATRES normally so victory/results can keep using every actor.
  6. Restores the borrowed 0xF800 bytes from the clean disc before returning
     control to the field.

The borrowed window is part of FFVII's static MDEC/VLC decode table. It is
needed for FMV decoding, but the supplied complete battle trace and every
Knights of the Round snapshot left it untouched throughout battle. DP6 does
not assume that the table is disposable: it restores the exact original 31
sectors after each battle that used temporary storage.

DP5 used func_80033FC4 for that restoration. Although its C signature resembles
a normal disc read, it is FFVII's streaming decompression reader (DS_read).
Both supplied DP5 crash dumps show it expanding the raw executable bytes past
0x80062000 and overwriting the SDK timer pointer at 0x80062B94. The next
GetRCnt call then faults at 0x80042C80.

DP6 uses func_80033F40, FFVII's synchronous raw sector reader. The requested
0xF800 bytes are copied verbatim to 0x80052800 without decompression.

The largest ordinary party body is Red XIII at 0xEED8 bytes. It fits with
0x928 bytes remaining. Across all 37,888 scanned layouts, the original actor
arena retains at least 0x9030 bytes before battle state.

If the party has fewer than three members, or does not contain HiCloud, DP6
keeps DP2's layout and performs no MDEC-table restore read.

WHY THE PATCHER REQUIRES THE CUE
--------------------------------
The clean executable's sector address is disc-specific. The CUE patcher:

  - identifies SCUS_941.63, SCUS_941.64, or SCUS_941.65 in the ISO;
  - verifies the PS-X EXE load address and the complete restore-window hash;
  - injects that exact restore-sector LBA into BATTLE.X;
  - verifies clean retail BATTLE.X and HICLOUD.LZS;
  - copies the BIN instead of modifying the original;
  - patches only the copied BATTLE.X and Palette64 HICLOUD.LZS sectors;
  - regenerates Mode 2 Form 1 EDC/ECC and reads everything back.

For safety, this package intentionally does not include a prepatched BATTLE.X
or a standalone drag-and-drop BATTLE.X patcher. A fixed restore LBA could read
the wrong 31 sectors on another disc layout.

RECOMMENDED USAGE
-----------------
1. Back up the save used for testing.
2. Extract this ZIP.
3. Keep the clean NTSC-U BIN and CUE together.
4. Drag the clean CUE onto Patch_FF7_Disc.bat.
5. Load the generated:

     Final Fantasy VII (Disc 1)_HighRes_Cloud.cue

Python 3 is required. The original BIN/CUE are not modified.

TEST ORDER
----------
Use a clean DP6 image, not DP3, DP4, or DP5.

1. Normal battle: HiCloud + Barret + Tifa.
2. Complete the results screen and return fully to the field.
3. Enter and complete a second normal battle.
4. Midgar Zolom: HiCloud + Red XIII + Vincent3.
5. Complete the Zolom results screen and return fully to the field.
6. Jenova-LIFE with the same party if the save permits it.
7. Cast Knights of the Round once in a large/boss battle.
8. After field return, play an available field or standalone FMV if practical.

Exercise attacks, menus, target selection, enemy actions, Limits/summons,
victory or escape, and field return. A short CD read pause after results is
expected only when DP6 used temporary storage.

If anything crashes, capture an immediate full 2 MiB main-RAM dump and note:

  - party and encounter;
  - the last action;
  - loading, active battle, results, restore pause, or field return;
  - whether a later FMV was involved.

PALETTE64
---------
The complete disc patcher also applies the established size-preserving
Palette64 conversion to HICLOUD.LZS.

HARDWARE VALIDATION
-------------------
DP6 passed on a standard 2 MiB target with the worst-case tested party:
HiCloud, Red XIII, and Vincent.

  - regular battle, results, and field return;
  - Midgar Zolom;
  - Jenova-LIFE;
  - Northern Crater battles through the final Sephiroth encounters;
  - Limits, Knights of the Round, other summons, and varied magic;
  - the final Cloud-versus-Sephiroth battle, which skips results and transitions
    directly into an FMV.

The direct battle-to-FMV transition is especially important: it confirms that
the borrowed MDEC/VLC window is restored in time for immediate movie playback.
As with any binary modification, additional reports from unusual game states
remain welcome.

BUILD DATE
----------
July 29, 2026

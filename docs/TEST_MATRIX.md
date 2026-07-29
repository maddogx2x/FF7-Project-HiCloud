# Test matrix

## DP6 golden 2 MiB build

Worst-case tested party: HiCloud, Red XIII, and Vincent.

| Scenario | Coverage | Result |
|---|---|---|
| Regular battle | active battle, results, field return | Passed |
| Midgar Zolom | large enemy model, battle completion | Passed |
| Jenova-LIFE | boss battle and effects | Passed |
| Northern Crater | battles leading through final encounters | Passed |
| Limits and magic | varied party and enemy actions | Passed |
| Knights of the Round | complete elaborate summon | Passed |
| Other summons | varied effect workloads | Passed |
| Final Cloud vs. Sephiroth | no results; direct FMV transition | Passed |

The final direct battle-to-FMV transition validates the critical lifetime
boundary: DP6 restored the borrowed MDEC/VLC table before movie decoding began.

## Automated validation

- 23 project regressions pass.
- 37 HiCloud party scenarios × 1,024 formations = 37,888 layouts fit.
- Minimum temporary-window margin: `0x928`.
- Minimum actor-arena margin: `0x9030`.
- Retail renderer ranges are byte-identical.
- The complete disc patch/read-back test passes.
- DP5 and DP6 deterministic builds differ only in the restore-reader call.

## Legacy 8 MiB coverage

The earlier 8 MiB + Palette64 build passed Midgar Zolom and several large
late-game battles on SuperStation One and DuckStation. It remains in the
repository for historical comparison, but DP6 is the recommended standard-RAM
build.

## Useful bug report data

- disc serial and source-image layout;
- platform/core or emulator version;
- battle/formation and party order;
- last action;
- whether load, active battle, results, restore pause, field return, or FMV
  playback failed;
- screenshot plus immediate 2 MiB RAM dump when available.

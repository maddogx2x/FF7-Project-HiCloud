# Test matrix

## Confirmed successful

| Scenario | Result |
|---|---|
| Midgar Zolom, 8 MiB + Palette64 | Loads, renders, battle completes |
| Midgar Zolom skybox | Horizontal strip absent |
| HiCloud eyes and belt | Correct in direct visual comparison |
| Several large late-game battles | Load successfully |
| DuckStation 8 MiB mode | Midgar Zolom loads |
| SuperStation One 8 MiB mode | Midgar Zolom loads |

## Earlier positional-prototype coverage

- Cloud in physical party slots 0, 1, and 2
- empty following player slot
- disc-2 party with no Cloud
- companion/model substitutions
- battle entry and completion in lighter formations

## Known unsupported configuration

Standard 2 MiB RAM remains unsafe for heavy formations. A successful small
battle is not evidence that the memory layout is globally safe.

## Useful bug report data

- disc serial and source-image layout
- platform/core or emulator version
- RAM mode
- battle/formation and party order
- whether entry, model load, action, victory, or cleanup failed
- screenshot plus RAM/VRAM dump when available

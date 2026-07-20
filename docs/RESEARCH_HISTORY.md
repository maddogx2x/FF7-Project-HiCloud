# Research history

The important experiments are retained here because the failures narrow the
remaining problem.

| Experiment | Observation | Conclusion |
|---|---|---|
| Original redirect on 2 MiB | Party-order-dependent corruption/crash | HiCloud exceeds the fixed player slot |
| Positional player packing | Light formations and slot permutations work | Direct player overlap is fixable |
| 8 MiB prototype | Midgar Zolom and large battles load | Main-RAM collision is the heavy-battle blocker |
| Original 8 MiB graphics | Cloud correct; skybox strip remains | RAM and VRAM problems are independent |
| CLUT relocation variants | Strip removed; eyes/belt corrupt | Background protected, model palette references incompatible |
| 4-bpp/layout experiments | Corruption or model-load crash | Format/layout conversion was not valid |
| Embedded CLUT-reference patch | Severe corruption/crash | Broad binary constant replacement is unsafe |
| Palette widths 240/224/208/192 | Strip persisted or improvement was insufficient | Upload still touched live background palette data |
| Palette width 64 | Strip gone; Cloud looks correct | First safe empty CLUT span confirmed |

The current release uses only the conclusions that survived direct runtime
testing: the stable 8 MiB battle resource plus the layout-preserving Palette64
archive.


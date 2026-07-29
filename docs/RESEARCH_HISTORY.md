# Research history

| Experiment | Observation | Conclusion |
|---|---|---|
| Original redirect on 2 MiB | Party-order-dependent corruption/crash | HiCloud exceeds the fixed player slot |
| Positional/dynamic packing | Light formations and slot permutations work | Direct player overlap is fixable |
| DP2 versus heavy formations | Worst case exceeds the actor arena by `0x5D84` | Packing alone does not create capacity |
| DP3/DP4 packet-bank reuse | GPU DMA-chain corruption | Apparently idle asynchronous memory is not safe |
| Full trace + KOTR snapshots | High-RAM candidates are active; MDEC/VLC window is battle-idle | Find a named owner with a non-overlapping lifetime |
| DP5 split storage | Combat, Zolom, and KOTR pass; results crash | Memory layout works, restoration call is wrong |
| DP5 crash forensics | Raw sectors expanded into timer globals | `func_80033FC4` is a decompression reader |
| DP6 raw restoration | Results, field return, bosses, final direct FMV pass | `func_80033F40` safely restores the borrowed table |
| Palette width 64 | Skybox strip gone; eyes and belt correct | Empty CLUT span confirmed |

DP6 combines only findings that survived direct evidence and runtime testing:
dynamic model pointers, split actor storage in a battle-idle MDEC/VLC window,
disc-specific raw restoration, the retail GPU renderer, and Palette64.

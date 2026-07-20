# Why standard 2 MiB RAM is still unsolved

## The confirmed collision

FF7's battle overlay treats player model storage as three fixed slots beginning
at `0x80103200`, with a `0xF000` stride. HiCloud's body/archive boundary is
`0x167F4`, so it extends `0x77F4` bytes beyond a normal slot. When the next
physical party slot is populated, that model can overwrite HiCloud's tail.
This explains why party order and an empty following slot change the outcome.

## What the positional prototype proved

One 2 MiB prototype packed the players contiguously:

| Region | Address |
|---|---:|
| HiCloud | `0x80103200` |
| Player 2 | `0x801199F4` |
| Player 3 | `0x801289F4` |
| Enemy arena | `0x801379F4` |

The enemy embedded pointer was adjusted from `0x80132580` to `0x80139D74`, and
the model copy/reload paths were changed to use a pointer table. Thirty-nine
verified 32-bit words in `BATTLE.X` changed. This passed Cloud in physical slots
0, 1, and 2, a disc-2 party without Cloud, and companion substitutions.

That establishes that direct player-to-player overwrite is real and can be
fixed. It does **not** create more memory. Midgar Zolom and other demanding
formations need enough enemy and battle-effect space that the relocated enemy
arena collides with later live allocations inside the same 2 MiB address space.

## Why a zero-filled range is not necessarily free

RAM dumps are snapshots, not ownership maps. Candidate ranges that appeared
empty were subsequently shown to be:

- `BATTLE.X` BSS or heap used later in the battle;
- model/decompression staging space, reusable only after a particular phase;
- effect, enemy, camera, or command data absent at the instant of the dump;
- storage addressed by hard-coded consumers not found by a simple constant
  search.

A prototype that moved a player to `0x80145504` overwrote live BSS and crashed.
The apparent staging area around `0x801B0000` is also overwritten later and
cannot hold a persistent player model.

## What a real 2 MiB solution likely requires

One of these approaches may work, but none is implemented yet:

1. A lifecycle-aware battle allocator that overlays regions only when their
   lifetimes provably do not overlap.
2. Streaming or reloading parts of the player model around attack animations.
3. Reducing HiCloud's geometry/animation footprint while preserving format and
   pointer invariants—not merely reducing its texture palette.
4. Reclaiming a proven persistent region after tracing every writer and reader
   across representative large formations.
5. A broader battle-overlay relink that moves all dependent pointers together.

The remaining problem is therefore capacity and lifetime management, not the
already-solved VRAM palette collision.


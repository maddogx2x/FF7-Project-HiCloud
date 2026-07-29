# How the standard 2 MiB solution works

## The original collision

FF7's battle overlay treats player-model storage as three fixed slots beginning
at `0x80103200`, with a `0xF000` stride. HiCloud reaches `0x167F4` bytes and can
overwrite the following player. This explains why party order and an empty
following slot changed the original patch's behavior.

## What dynamic packing solved

DP2 replaced fixed-stride reconstruction with a dynamic pointer table and
completed party layout before enemy decompression. That eliminated direct
player-to-player overwrite and worked across party-slot permutations.

It did not create more RAM. Across all 1,024 formations, the worst three-member
HiCloud layout could exceed the remaining actor arena by `0x5D84` bytes.
Attempts to move the complete arena or borrow a GPU packet bank failed because
the candidate regions had later or asynchronous owners.

## Finding a reversible owner

A 28.8 GiB vanilla trace covered 362,887,887 instructions and 98,240,133
memory accesses. It was combined with synchronized full-RAM snapshots taken
before, during, and after Knights of the Round.

That evidence identified `0x80052800–0x80062000`:

- size: `0xF800` bytes;
- owner: the main executable's static MDEC/VLC decode table;
- battle behavior: untouched through loading, active combat, summons, results,
  and field return;
- future requirement: needed again for FMV decoding.

The important insight was not that the window was free. It had a known owner
whose lifetime did not overlap battle, and its exact original bytes could be
restored before that owner resumed.

## DP6 layout

For a full three-member HiCloud party, DP6:

1. keeps HiCloud and one ordinary teammate in the dynamic actor arena;
2. puts the third sorted ordinary model in the MDEC/VLC window;
3. loads enemies immediately after the two arena-resident party models;
4. leaves the retail double-buffered GPU renderer unchanged;
5. runs results before restoring, so all actor pointers remain valid;
6. synchronously reloads the exact 31 original executable sectors before
   returning to the field.

The patcher locates the main executable on each disc, verifies the restore
window, derives its LBA, and injects that disc-specific value into `BATTLE.X`.
It intentionally does not distribute a fixed-LBA prepatched executable.

## Capacity

The largest ordinary party body is Red XIII at `0xEED8`, leaving `0x928` bytes
in the `0xF800` temporary window. Across 37 HiCloud party scenarios and all
1,024 formations—37,888 layouts total—the minimum actor-arena margin is
`0x9030`.

## DP5 failure and DP6 correction

DP5 proved the split layout in normal combat, Midgar Zolom, and Knights of the
Round, but crashed during results. It called `func_80033FC4`, FFVII's streaming
decompression reader, to restore raw executable sectors. The decoder expanded
those bytes past `0x80062000` and corrupted the timer pointer at `0x80062B94`.

DP6 calls `func_80033F40`, FFVII's synchronous raw-sector reader. It copies the
requested `0xF800` bytes verbatim and blocks field return until restoration
completes.

## Hardware result

DP6 passed with HiCloud, Red XIII, and Vincent in regular combat, Midgar Zolom,
Jenova-LIFE, and the Northern Crater through the final Sephiroth encounters.
Limits, Knights of the Round, other summons, varied magic, results, field
return, and the final direct battle-to-FMV transition all passed on 2 MiB.

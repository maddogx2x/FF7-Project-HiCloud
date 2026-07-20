# VRAM collision and the Palette64 fix

HiCloud contains an 8-bpp TIM at decompressed offset `0x167F4`. Its texture
image is 128×192 indexed pixels, stored in the PSX TIM image rectangle
`(x=320, y=0, w=64 words, h=192)`. The original CLUT upload begins at
`(x=320, y=192)` and declares 256 entries, although 198 colors are used.

In the affected battle, that wide upload overwrites palette data used by the
battle background. The result is the horizontal skybox strip. Early experiments
that relocated or changed the CLUT removed the strip but broke Cloud's eyes and
belt because the model's draw primitives still sampled incompatible palette
coordinates or formats.

The successful experiment left the TIM/archive layout and CLUT origin alone and
reduced the upload width to 64 entries. The first four 16-color blocks in the
relevant party-adjusted CLUT rows were empty in the comparison dumps, so this
keeps the upload within that unused span.

## Quantization result

| Measure | Result |
|---|---:|
| Original used colors | 198 |
| Final palette width | 64 |
| Changed texels | 989 / 24,576 |
| Maximum 8-bit display-channel delta | 41 |
| Opaque texels mapped to transparent | 0 |

The converter preserves PSX STP-bit classes and transparent zero. It keeps the
original 524-byte CLUT block and every following archive offset fixed; unused
palette words remain inert. Geometry, UVs, texture size, 8-bpp mode, and model
data are unchanged.

Although 64 colors began as an extreme diagnostic, direct testing found Cloud's
eyes and belt visually correct while the skybox strip disappeared. We therefore
kept the safer 64-entry boundary rather than moving back toward the collision.


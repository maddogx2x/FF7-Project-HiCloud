# Contributing

The highest-value contribution is a reproducible standard-2-MiB solution.

Please keep contributions clean-room and patch-only. Do not open issues or pull
requests containing disc images, complete extracted game files, BIOS images,
RAM/VRAM dumps with copyrighted content, or compiled third-party FPGA cores.

For memory-layout work, document:

- exact source hashes and disc serial;
- every changed address/word and why it is a pointer, size, or consumer;
- the lifetime of any region claimed as free;
- tests with Cloud in all physical slots, no-Cloud parties, and large enemy
  formations;
- entry, actions, victory, and post-battle cleanup—not only model load.

For texture changes, preserve transparency and PSX STP semantics and report
palette width, changed texels, maximum error, TIM offsets, compressed size, and
a before/after VRAM comparison.


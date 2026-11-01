# gbtest: headless test and profiling tools

Runs jagoombacolor builds with Game Boy test ROMs inside libmgba (mGBA 0.10.3) without a GUI.
Results include exact GBA cycle counts, so the same run serves as a regression and performance check.

## Setup

    tools/gbtest/build.sh        # clones mGBA 0.10.3, builds a static GBA-only libmgba and build/gbaharness
    tools/gbtest/fetch_roms.sh   # downloads c-sp/game-boy-test-roms v7.0 into roms/

Requires git, cmake, a C compiler, Python 3 and devkitARM (for `arm-none-eabi-nm`/`objdump`).

## Test suites

    python3 tools/gbtest/runtests.py --emu jagoombacolor.gba --elf jagoombacolor.elf \
        --roms tools/gbtest/roms --out tools/gbtest/out/NAME [--game path/to/game.gbc] [--filter REGEX]

Suites and pass criteria (from each suite's `game-boy-test-roms-howto.md`):

| Suite | Exit | Pass |
|---|---|---|
| Blargg (combined ROMs) | fixed emulated time (documented time x1.5 + 2 s) | screenshot |
| Mooneye, SameSuite, AGE | opcode `LD B,B` | registers B,C,D,E,H,L = 3,5,8,13,21,34 |
| Mooneye (wilbertpol) | opcode `0xED` | same registers |
| dmg-acid2, cgb-acid2, Mealybug | `LD B,B` | screenshot |
| GBMicrotest | 1 s of emulated time | `$FF82 == $01` |
| `--game` | fixed GB frames | none: busy cycles and screenshots for review |

Screenshots are compared "up to recolouring": Goomba uses its own palettes, so a test passes when every
reference colour maps to exactly one output colour and vice versa. The GB screen sits at (40,8) in
Goomba's 240x160 frame. Gambatte's suite is not wired up yet (needs its hex-digit patterns).

Each result records `gba_cycles` (busy cycles: steps longer than 1000 cycles count as idle halt/vblank
waits) and `gb_frames`. Runs are deterministic for a given build.

Compare two runs (status changes, busy cycles per GB frame):

    python3 tools/gbtest/diffruns.py out/base/results.json out/new/results.json [MIN_GB_FRAMES]

Noise floor: moving code around without changing what runs (e.g. a few unreachable instructions) shifts
busy cycles per GB frame by about 0.1% on Polished Crystal and about 0.2% over test ROMs with at least
300 GB frames (the default); short tests can move by tens of percent. Treat smaller differences as noise.
Game checkpoints stop on exact GB frames, so their screenshots can be compared between builds.

## Profiling

Exact cycle profile (every ARM instruction stepped and timed):

    python3 tools/gbtest/profile.py --emu jagoombacolor.gba --elf jagoombacolor.elf \
        --rom game.gb [--skip GBFRAMES] [--frames GBAFRAMES] [--annotate SYMBOL ...]

## Notes on Goomba under test

- Goomba already executes GB code during the ROM-menu fade-in (`run(0)`), before `run(1)`. The runner
  watches from before the first GB instruction; tests can finish within their first few frames.
- Because `run(0)` restarts the GB frame, how much GB time passes during start-up depends on GBA speed.
  Each build is deterministic, but a test can land a GB frame or two apart between builds, so
  `diffruns.py` compares busy cycles per GB frame rather than totals.
- Tests run in Goomba's normal (throttled) mode, as on hardware. Unthrottled mode (`novblankwait`,
  L+Start) is only cleared by `run()`, so it must be set after `run(1)` starts; whether it changes
  GB-visible behaviour has not been checked.

## Harness protocol

`build/gbaharness ROM.gba` reads commands on stdin and answers each with output lines followed by `ok`.
See the comment at the top of `gbaharness.c`; `harness.py` wraps it for Python.

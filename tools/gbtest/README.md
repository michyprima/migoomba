# gbtest: headless test and profiling tools

Runs jagoombacolor builds with Game Boy test ROMs inside libmgba (mGBA 0.10.3) without a GUI.
Results include exact GBA cycle counts, so the same run serves as a regression and performance check.

## Setup

    tools/gbtest/build.sh        # clones mGBA 0.10.3, builds a static libmgba, build/gbaharness and build/gbref
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

Each result records `gba_cycles` (busy cycles: time with the CPU halted in IntrWait/Halt counts as idle, as
do single steps longer than 1000 cycles) and `gb_frames`. Runs are deterministic for a given build.

Compare two runs (status changes, busy cycles per GB frame):

    python3 tools/gbtest/diffruns.py out/base/results.json out/new/results.json [MIN_GB_FRAMES]

Noise floor: moving code around without changing what runs (e.g. a few unreachable instructions) shifts
busy cycles per GB frame by about 0.1% on Polished Crystal and about 0.2% over test ROMs with at least
300 GB frames (the default); short tests can move by tens of percent. Treat smaller differences as noise.
Game checkpoints stop on exact GB frames, so their screenshots can be compared between builds.

## Real games

    python3 tools/gbtest/gametests.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out tools/gbtest/out/games \
        [--filter REGEX] [--record]

Commercial ROMs are never committed. Put them, under any file name, in `tools/gbtest/games/` (gitignored;
`--games DIR` for another folder). Each ROM is identified by its SHA-1 and looked up in `games.json`, which is
committed and holds, per game, the title, input script, the model Goomba chose, Goomba's busy cycles per GB
frame and, per checkpoint, hashes of Goomba's and the reference's GB screen. No images are stored.

Every game runs the same input script (`start-a`: wait for the intro, alternate Start and A for 20 s, then
leave it alone; screenshots at seven GB frames up to 2400) in two emulators:

- **Goomba**: each checkpoint screen must hash to the recorded one. A difference is `CHANGED` (a regression,
  or an intended change: inspect the shots in `--out`, then accept with `--record`). `HANG` means GB frames
  stopped advancing. Runs are deterministic.
- **Reference**: `build/gbref`, mGBA's own GB core, on the model Goomba picked (CGB, SGB or DMG). The `ref`
  column counts checkpoints where Goomba's screen equals the reference up to recolouring. Input timing can
  differ by a frame between the two, so this is informational: a lead to look at, not a failure. SGB games
  differ wherever Goomba's SGB palettes or packet timing do.

`--record` stores the current results for the games run (all, or those matching `--filter`). Unknown ROMs
show as `NEW`; games in the database whose ROM is missing are listed. Exit status is 0 only if every game is
`PASS`. `busy/fr` above 280,896 means the game runs slower than real time on a GBA.

## Battery saves

    python3 tools/gbtest/savetest.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out tools/gbtest/out/save

Builds small ROMs that keep a counter in cart RAM (MBC2, and MBC5 with 32KB of RAM using bank 3), then
runs them across simulated power cycles (each harness run is one power-on; the GBA battery save is kept
between runs), around Goomba's quick save / quick load hotkeys (R+Select / R+Start) and around opening
and closing the menu, with 32KB of GBA SRAM (mGBA's usual) and 64KB (like most flash carts).
`gbaharness ROM.gba SAVE.sav [64]` keeps the save file; 64 makes it 64KB of SRAM.

How Goomba keeps cart RAM (src/sram.c): saved states and every game's packed (LZO) cart RAM fill GBA
SRAM from the start; the running game's cart RAM is also written through to the end, so it survives a
power-off and is packed on the next boot. Up to 8KB it lives at the last 8KB. 32KB cart RAM lives at
$8000-$FFFF while the game runs, on 64KB GBA SRAM when everything else fits below $8000 (with 4KB to
spare); the menu packs it and gives the whole SRAM back until the game resumes. Otherwise (32KB GBA
SRAM, or too many saves) 32KB cart RAM is only saved when the menu opens.

`tools/goombasave.py` moves a game's cart RAM between Goomba's GBA save and a plain Game Boy save
(cartridge dumps, EverDrive GB, emulators); `goombasave_test.py` checks it with Goomba itself:

    python3 tools/gbtest/goombasave_test.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out tools/gbtest/out/gsave

## Stack headroom (run after any change that adds IWRAM code or data)

    python3 tools/gbtest/stackcheck.py --emu jagoombacolor.gba --elf jagoombacolor.elf \
        --roms tools/gbtest/roms --out tools/gbtest/out/stack --game path/to/game.gbc

IWRAM is nearly full and .bss ends just below the user stack. The deepest stack in normal play is the
VBlank handler, which runs in system mode on top of the emulation loop's stack (about 300 bytes).
Below .bss's end sits CHR_DECODE, the tile decode table; a stack that reaches it makes tiles decode
wrongly until a canary triggers a rebuild. Until 2026-11-01 the stack overran it by up to 168 bytes
(24 more bytes of IWRAM broke cpu_instrs); moving rarely-run code to ROM fixed that, leaving 80 bytes
spare. The check steps every instruction of real workloads (cpu_instrs, quick save/load, any --game),
reports the deepest stack and where it happened, and fails with less than --min-spare bytes (32).
Keep code that runs at most a few times per frame in .text (ROM) rather than IWRAM.

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

# Working on jagoombacolor

Goomba Color fork: a Game Boy / Game Boy Color emulator that runs on the GBA, mostly ARM assembly.
The goal is compatibility and accuracy without costing GBA cycles. Pixel-perfect output is not a goal:
"good enough on screen, cheaper" usually wins. Real-hardware testing happens on GBA; mGBA results are a
proxy, so keep cycle-sensitive choices conservative.

## Build

    make                      # needs DEVKITARM; produces jagoombacolor.gba + jagoombacolor.elf

- make compares 1-second mtimes. After `git stash`/`checkout` in the same second, `touch src/all.s`
  (or `make clean`) before building, or you test a stale binary.
- `src/all.s` #includes every `.s` file into one object, so literal pools are shared per section:
  when `.text` grows you may get "pool needs to be closer"; add a `.ltorg` near the failing line
  (after an unconditional branch/return).
- The linker's "LOAD segment with RWX permissions" warning is normal.

## Line endings

`src/` and the Makefile are CRLF, with global `core.autocrlf=input`. The Edit tool keeps CRLF. Scripts
must read/write with `newline=''` (Python) and keep `\r\n`. Check after editing:
`git diff --stat` and `git diff --stat --ignore-cr-at-eol` must show the same counts.

## Core layout (what bites)

- ARM register map in the CPU core: `gb_flg` r3, `gb_a` r4, `gb_bc` r5, `gb_de` r6, `gb_hl` r7,
  `cycles` r8, `gb_pc` r9, `globalptr` r10, `gb_sp` r11, `addy` r12. `ldr_`/`str_`/`ldrb_` macros
  address globals relative to `globalptr`.
- `cycles` counts down to the end of the scanline; the low 4 bits are flags (`CYC_IE`, `CYC_LCD_ENABLED`,
  `CYC_HALTED`). `CYCLE` = 16. Effective line, as tobuffer counts it: `scanline`, +1 when
  `cycles < scanline_oam_position` (HBlank); VBlank counts as line 0.
- Timers, STAT and LYC are evaluated once per scanline (timeout.s); there are no mid-line events.
- `blne_long`/`bl_long` clobber lr; `b_long` from IWRAM/VRAM needs a pool within 4 KB (`ldr pc,=label`).
- Display: per-line HBlank DMA 0-2 (BGxCNT/scroll, DISPCNT, WIN0H) armed at GBA line 7 (`do_gba_hdma`)
  and stopped at line 151 (`end_gba_hdma`). A DMA only re-latches its source on a 0->1 enable, so a missed
  stop corrupts every later frame. Per-line palettes (DMG BGP, CGB hi-colour) use a VCOUNT/HBlank IRQ
  chain (`bgp_vcount`, `hblank_palette`, `cgbp_*` in lcd.s).
- Code placement is a budget: IWRAM is full, `.vram1` (0x0600F000, 3584 bytes) is nearly full, ROM code
  costs ~7.5 cycles/instruction. Hot paths in IWRAM/VRAM, rarely-run code in `.text`. Check sizes with
  `arm-none-eabi-size -A jagoombacolor.elf`.
- After any IWRAM/stack change run `tools/gbtest/stackcheck.py` (needs >= 32 bytes spare).
- EWRAM between the heap start and `Next` (equates.h) is borrowed by the save code (`sram_copy`, LZO
  workspace, compressed buffers, from `ewram_start`) and by multiboot for the ROM; saves run past `Next`
  and `cleanup_ewram()` repairs what they hit. Don't grow `.sbss` (it moves the heap start): put
  emulator buffers in equates.h below `Next` and make them safe to clear in `cleanup_ewram()`.
- Battery saves: see "Battery saves" in tools/gbtest/README.md (live areas in GBA SRAM, `savetest.py`).

## Testing (tools/gbtest, see its README)

Set up once with `tools/gbtest/build.sh` and `fetch_roms.sh`. Runs are deterministic per build.

- Test ROMs: `runtests.py --emu jagoombacolor.gba --elf jagoombacolor.elf --roms tools/gbtest/roms --out DIR`
  (~900 tests, ~1 min); compare two runs with `diffruns.py A/results.json B/results.json`, which also
  reports busy cycles per GB frame. Noise floor ~0.1-0.2%.
- Real games: `gametests.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out DIR [--filter RE] [--record]`.
  ROMs live in gitignored `tools/gbtest/games/`, identified by SHA-1 in `games.json`; each run is compared
  with Goomba's recorded screens and with mGBA's GB core (`build/gbref`) as reference. `busy/fr` above
  280,896 means slower than real time.
- Profiling: `profile.py --rom ...`. Harness (`build/gbaharness`) and gbref take line commands on stdin
  (documented at the top of each .c file): `until`, `trace`, `watch`, `dma`, `regs`; gbref `iolog`
  (register writes with LY/PC), `vram`, `cgbpal`, `peek`. `harness.py` wraps them in Python.
- Debugging a broken game: compare GB VRAM (Goomba's `XGB_VRAM` vs gbref `vram`) first. Equal means the
  bug is on the GBA side (tile/map conversion, palettes, per-line registers, DMA); different means
  emulation/timing. Then log the game's register writes with gbref `iolog` and compare per scanline.

Before committing an emulator change: test ROMs (no unexplained PASS->FAIL), all games (inspect every
CHANGED screen against the old build and the reference, then `--record`), stackcheck, and report the
cycle cost per game.

## How we work

- Commercial ROMs and game screenshots are never committed or published (no artifacts with game
  images); keep comparison pages local. Scratch files go in the session scratchpad.
- Branch: stay on the current working branch (`fix/jr-wrap`); don't create branches or push unless asked.
- Commit tooling (tools/gbtest) separately from emulator fixes, one fix per commit, each commit building.
  Messages: a short subject, then what was wrong (with the game/test that shows it), what changed, and
  the measured cost and test changes. Re-record `games.json` in its own commit.
- Measure, don't guess: state cycle costs from runs (per game, vs the previous build), and say when a
  number is within noise. Explanations get verified before being reported; correct earlier claims
  openly when they turn out wrong.
- Prefer zero-cost or rarely-run fixes; anything on a per-instruction or per-scanline path must be
  justified by measurements.

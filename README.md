# MiGoomba

A Game Boy / Game Boy Color emulator for the Game Boy Advance. MiGoomba is a fork of
[Jagoomba Color](https://github.com/EvilJagaGenius/jagoombacolor) by Jaga, itself a fork of Dwedit's
Goomba Color (2019-05-04 source), and it keeps the same goal: fix bugs and incompatibilities without
making the emulator slower. Pixel-perfect accuracy is not a goal; the cost on the GBA is measured for
every change.

Everything below was measured in mGBA through the headless test tools in `tools/gbtest`; checks on
real hardware (EZ-Flash Omega Definitive Edition) are still to come.

## What's new compared with Jagoomba Color

### CPU and timing
- **JR across $FFFF / $0000** wraps correctly in both directions (Polished Crystal crashed).
- **HALT bug** emulated (HALT with interrupts disabled and one pending runs the next byte twice).
- **EI;DI** no longer takes a pending interrupt in between.
- **Interrupt dispatch onto IE**: pushing PC over $FFFF changes IE and the dispatch, as on hardware.
- **Power-on values** for JOYP, SB, IF and DIV.
- **Timer**: the interrupt fires at the scanline start nearest the real overflow (it used to come at
  the end of the line, rounding every period up), and TIMA reads and writes account for the position
  in the line. Games that time raster effects with the timer (Alone in the Dark) now line up.

### Memory, cartridges and I/O
- **MBC1** mode 1 and **MBC1M** multicarts (bank $10 logo detection).
- **MBC2**: register decoding, the built-in 512 x 4-bit RAM, and saves that keep working after
  loading a state.
- Reads with cart RAM disabled return $FF; unused I/O bits read as 1; CGB-only registers read $FF in
  DMG mode; $FF46 returns the last value written.
- **OAM DMA** from echo RAM; OAM reads after a frame no longer return stale data.

### Video
- **Per-line DMG palettes**: games that rewrite BGP mid-frame (V-Rally's race screen, Lemmings 2) get
  their palette changes on the right lines.
- **Per-line CGB palettes ("hi-colour")**: games that rewrite BG palettes on many lines (Rainbow
  Islands' logo, Tomb Raider, Turok 3, Alone in the Dark, Worms Armageddon) display correctly. Lines
  with identical palettes are merged, and a recorded table is reused on the next frame to keep the
  cost down.
- **DMA to the BG maps** in 16-byte blocks marks the right columns as changed (Turok 3's screens had
  their right half missing).
- The display DMAs are re-armed every frame, so a disturbed raster effect can no longer leave the
  screen striped.

### Saves
- **32KB cart RAM survives a power-off** (Pokemon Gold/Silver/Crystal, Polished Crystal, ...). Goomba
  used to write it to the GBA save only when its menu was opened, so saving in the game and switching
  off lost the save. With 64KB of GBA SRAM, the running game's cart RAM is now written through to the
  GBA save as it changes. See [Saves](#saves).
- **`tools/goombasave.py`** moves saves between MiGoomba and plain Game Boy saves (real cartridges,
  EverDrive GB, emulators). See [Moving saves](#moving-saves).

### Robustness and speed
- Per-scanline bookkeeping trimmed (-1.6% emulation time on Polished Crystal); 240 bytes of IWRAM
  freed so the stack no longer overwrites the tile decode table.
- The EWRAM layout keeps the per-line palette data clear of the memory the save code borrows.
- Builds with GCC 14 and later, in one pass.

## Compatibility

### Test ROM suites
From [c-sp/game-boy-test-roms](https://github.com/c-sp/game-boy-test-roms) v7.0, run headless in mGBA
(`tools/gbtest/runtests.py`). Jagoomba Color was built from its last commit with only the two build
fixes applied.

| Suite | Jagoomba Color | MiGoomba |
|---|---|---|
| Blargg | 1/9 | 1/9 |
| Mooneye Test Suite | 29/112 | 43/112 |
| Mooneye (wilbertpol) | 8/116 | 11/116 |
| GBMicrotest | 75/513 | 137/513 |
| SameSuite | 1/78 | 1/78 |
| AGE | 0/47 | 0/47 |
| Mealybug Tearoom | 0/35 | 0/35 |
| cgb-acid2 / dmg-acid2 | 0/2 | 0/2 |
| **Total** | **114/912** | **193/912** |

Blargg's cpu_instrs passes. Most remaining failures need cycle-exact timing (PPU modes within a
scanline, the DIV/timer coupling), which Goomba trades for speed.

### Games
100 commercial games picked for being hard to emulate, from many regions (USA, Europe, Japan, Korea
and single-country releases), all run through the same input script (wait for the intro, alternate
Start and A for 20 seconds, screenshots at 7 moments up to Game Boy frame 2400) by
`tools/gbtest/gametests.py`. ROMs are not part of the repository; they are identified by hash.

- All 100 boot and run without hangs, on both builds.
- **Reference**: screenshots that match mGBA's own Game Boy core (on the CGB, SGB or DMG model
  MiGoomba picks), allowing for different colours: **365/700 for Jagoomba Color, 401/700 for
  MiGoomba**. 14 games improve, none get worse, 16 match at every checkpoint. Input timing differs by
  a frame or so between the two emulators, and SGB games differ wherever borders and palettes do, so a
  low number is a lead to look at rather than a failure (Adventures of Lolo plays fine but differs in
  its SGB colours).
- **GBA load**: emulation time per Game Boy frame as a share of a real GBA's frame time. Above 100%
  the game runs slower than full speed: Alfred's Adventure, Alone in the Dark (just), Harry Potter,
  Perfect Dark, Pocket Family GB 2, Robot Poncots, SWIV and Tomb Raider.

Games that improved: Turok 3 (1 -> 7 of 7 checkpoints), Alone in the Dark (5 -> 7), Tomb Raider
(2 -> 6), Suzuki Alstare Extreme Racing (1 -> 5), Inspector Gadget (2 -> 5), Medarot 5 (3 -> 6),
Bomberman Selection (0 -> 3), Rayman 2 (0 -> 3), Carmageddon (2 -> 4), Driver (4 -> 6), and one more
checkpoint each for Cannon Fodder, Donkey Kong Country, Gradius: The Interstellar Assault and Robin
Hood.

Known issues: no tilt input for Korokoro Kirby (MBC7) and no infrared for Pocket Family GB 2 and
Chee-Chai Alien; 3-D Ultra Pinball and The Smurfs' Nightmare differ from the reference at every
checkpoint and have not been looked into yet.

<details>
<summary>All 100 games</summary>

| Game | Mode | Reference (Jagoomba Color) | Reference (MiGoomba) | GBA load |
|---|---|---|---|---|
| 3-D Ultra Pinball - Thrillride (USA) (Rumble Version) | CGB | 0/7 | 0/7 | 49% |
| Adventures of Lolo (Europe) (SGB Enhanced) | SGB | 0/7 | 0/7 | 41% |
| Alfred's Adventure (Europe) (En,Fr,De,Es,It) | CGB | 1/7 | 1/7 | 140% |
| Alone in the Dark - The New Nightmare (USA) | CGB | 5/7 | 7/7 | 100% |
| Asterix - Search for Dogmatix (Europe) | CGB | 7/7 | 7/7 | 39% |
| Baby Felix - Halloween (Europe) (En,Fr,De,Es,It,Nl) | CGB | 5/7 | 5/7 | 41% |
| Beatmania GB - Gotcha Mix 2 (Japan) | CGB | 7/7 | 7/7 | 34% |
| Beavis and Butt-Head (USA) | DMG | 7/7 | 7/7 | 28% |
| Bionic Commando - Elite Forces (USA) | CGB | 3/7 | 3/7 | 63% |
| Bomberman Selection (Korea) | CGB | 0/7 | 3/7 | 29% |
| Cannon Fodder (Europe) (En,Fr,De,Es,It) | CGB | 4/7 | 5/7 | 70% |
| Carmageddon - Carpocalypse Now (USA, Europe) (En,Fr,Es,It) | CGB | 2/7 | 4/7 | 70% |
| Chee-Chai Alien (Japan) (Rumble Version) | CGB | 0/7 | 0/7 | 33% |
| Choplifter III (Europe) | DMG | 7/7 | 7/7 | 26% |
| Croc 2 (USA, Europe) | CGB | 5/7 | 5/7 | 32% |
| Dance Dance Revolution GB (Japan) | CGB | 6/7 | 6/7 | 45% |
| Densha de Go! 2 (Japan) | CGB | 2/7 | 2/7 | 35% |
| Donkey Kong Country (USA) | CGB | 2/7 | 3/7 | 58% |
| Donkey Kong Land (USA) (SGB Enhanced) | SGB | 0/7 | 0/7 | 40% |
| Dragon Ball Z - Guerreros de Leyenda (Spain) | CGB | 6/7 | 6/7 | 31% |
| Dragon Ball Z - I Leggendari Super Guerrieri (Italy) | CGB | 5/7 | 5/7 | 31% |
| Dragon Warrior III (USA) | CGB | 6/7 | 6/7 | 52% |
| Driver (Europe) (En,Fr,De,Es,It) | CGB | 4/7 | 6/7 | 55% |
| F1 Championship Season 2000 (Brazil) (En,Fr,De,Es,It) | CGB | 7/7 | 7/7 | 97% |
| Faceball 2000 (USA) | DMG | 7/7 | 7/7 | 54% |
| FIFA 98 - Road to World Cup (Europe) (Rev-A) (SGB Enhanced) | SGB | 0/7 | 0/7 | 25% |
| Gradius - The Interstellar Assault (USA) | DMG | 5/7 | 6/7 | 40% |
| Halloween Racer (Europe) | CGB | 2/7 | 2/7 | 69% |
| Harry Potter and the Sorcerer's Stone (USA) | CGB | 4/7 | 4/7 | 134% |
| Harvest Moon 3 GBC (USA) | CGB | 6/7 | 6/7 | 39% |
| Inspector Gadget - Operation Madkactus (Europe) (En,Fr,De,Es,It,Nl) | CGB | 2/7 | 5/7 | 60% |
| John Romero's Daikatana (Europe) | CGB | 5/7 | 5/7 | 48% |
| Keitai Denjuu Telefang - Power Version (Japan) (GB Compatible) | CGB | 7/7 | 7/7 | 31% |
| King of Fighters, The - Heat of Battle (Europe) (SGB Enhanced) | SGB | 1/7 | 1/7 | 65% |
| Kirby's Dream Land 2 (USA) (SGB Enhanced) | SGB | 0/7 | 0/7 | 37% |
| Konami GB Collection Vol.4 (Europe) (GB Compatible) | CGB | 3/7 | 3/7 | 49% |
| Korokoro Kirby (Japan) | CGB | 2/7 | 2/7 | 76% |
| Legend of Zelda, The - Link's Awakening DX (USA, Europe) (Rev 2) (SGB Enhanced) (GB Compatible) | CGB | 2/7 | 2/7 | 40% |
| Legend of Zelda, The - Oracle of Ages (USA) | CGB | 7/7 | 7/7 | 34% |
| Lemmings 2 - The Tribes (Europe) | DMG | 5/7 | 5/7 | 41% |
| Lucky Luke - Desperado Train (Europe) | CGB | 5/7 | 5/7 | 96% |
| Lufia - The Legend Returns (USA) | CGB | 4/7 | 4/7 | 66% |
| Magi Nation (USA) | CGB | 5/7 | 5/7 | 34% |
| Medarot 5 - Susutake Mura no Tenkousei - Kabuto (Japan) | CGB | 3/7 | 6/7 | 64% |
| Mega Man Xtreme (USA) | CGB | 7/7 | 7/7 | 51% |
| Metal Gear - Ghost Babel (Japan) | CGB | 4/7 | 4/7 | 51% |
| Metal Gear Solid (USA) | CGB | 6/7 | 6/7 | 46% |
| Micro Machines 2 - Turbo Tournament (Europe) | DMG | 3/7 | 3/7 | 36% |
| Monster Max (Europe) | DMG | 6/7 | 6/7 | 39% |
| Mr. Driller (USA) | CGB | 2/7 | 2/7 | 92% |
| Mr. Nutz (Europe) | DMG | 1/7 | 1/7 | 31% |
| Net de Get - Minigame @ 100 (Japan) | CGB | 1/7 | 1/7 | 69% |
| Oddworld Adventures II (USA, Europe) (En,Fr,De,Es,It) | CGB | 5/7 | 5/7 | 53% |
| Parodius (Europe) | DMG | 2/7 | 2/7 | 43% |
| Perfect Dark (USA) | CGB | 3/7 | 3/7 | 165% |
| Pinball Fantasies (USA) | DMG | 6/7 | 6/7 | 26% |
| Pocket Family GB 2 (Japan) | CGB | 0/7 | 0/7 | 117% |
| Pocket Music (Europe) (En,Fr,De,Es,It) | CGB | 5/7 | 5/7 | 61% |
| Pokemon - Gold Version (USA, Europe) (SGB Enhanced) (GB Compatible) | CGB | 6/7 | 6/7 | 48% |
| Pokemon Card GB 2 - GR Dan Sanjou! (Japan) | CGB | 6/7 | 6/7 | 46% |
| Pokemon Crystal Version (USA) (Rev-A) | CGB | 7/7 | 7/7 | 43% |
| Pokemon Pinball (USA) | CGB | 1/7 | 1/7 | 48% |
| Pokemon Yellow Version - Special Pikachu Edition (USA) (SGB Enhanced) | CGB | 6/7 | 6/7 | 57% |
| Pop'n TwinBee (Europe) | DMG | 6/7 | 6/7 | 62% |
| Prince of Persia (USA, Europe) (En,Fr,De,Es,It) (GB Compatible) | CGB | 1/7 | 1/7 | 53% |
| R-Type II (Europe) | DMG | 4/7 | 4/7 | 46% |
| Rainbow Islands (Europe) (En,Fr,De,Es,It) | CGB | 4/7 | 4/7 | 38% |
| Rayman 2 (USA) (En,Fr,De,Es,It) | CGB | 0/7 | 3/7 | 52% |
| Resident Evil Gaiden (Europe) (En,Fr,De,Es,It) | CGB | 7/7 | 7/7 | 48% |
| Road Rash (USA) | DMG | 2/7 | 2/7 | 23% |
| Robin Hood (Europe) (En,Fr,De,Es,It,Nl) | CGB | 6/7 | 7/7 | 33% |
| Robot Poncots - Sun Version (Japan) (SGB Enhanced) (GB Compatible) | CGB | 3/7 | 3/7 | 117% |
| Sakura Taisen GB 2 - Thunderbolt Sakusen (Japan) | CGB | 3/7 | 3/7 | 36% |
| Sensible Soccer - European Champions (Europe) | DMG | 3/7 | 3/7 | 22% |
| Shantae (USA) | CGB | 3/7 | 3/7 | 79% |
| Shin Megami Tensei Devil Children - Shiro no Sho (Japan) | CGB | 5/7 | 5/7 | 62% |
| Smurfs Nightmare, The (Europe) (En,Fr,De,Es) | CGB | 0/7 | 0/7 | 29% |
| Space Station Silicon Valley (Europe) | CGB | 2/7 | 2/7 | 33% |
| Star Hawk (Europe) | DMG | 3/7 | 3/7 | 49% |
| Star Wars Episode I - Racer (USA) | CGB | 5/7 | 5/7 | 36% |
| Super Mario Bros. Deluxe (USA) (Rev-B) | CGB | 7/7 | 7/7 | 37% |
| Super Mario Land 2 - 6 Golden Coins (USA) (Rev-B) | DMG | 2/7 | 2/7 | 31% |
| Suzuki Alstare Extreme Racing (Europe) (En,Fr,De,Es,It,Nl) | CGB | 1/7 | 5/7 | 71% |
| SWIV (Europe) (En,Fr,De,Es,It) | CGB | 5/7 | 5/7 | 135% |
| Test Drive 2001 (USA) | CGB | 4/7 | 4/7 | 68% |
| Tintin in Tibet (Europe) (SGB Enhanced) | SGB | 0/7 | 0/7 | 35% |
| Toki Tori (USA, Europe) (En,Ja,Fr,De,Es) | CGB | 5/7 | 5/7 | 27% |
| Tomb Raider (USA) | CGB | 2/7 | 6/7 | 162% |
| Top Gear Pocket (USA) | CGB | 4/7 | 4/7 | 73% |
| Trip World (Europe) | DMG | 3/7 | 3/7 | 19% |
| Turok 3 - Shadow of Oblivion (USA) | CGB | 1/7 | 7/7 | 84% |
| V-Rally - Championship Edition (Europe) | DMG | 0/7 | 0/7 | 56% |
| Vigilante 8 (USA) (Rumble Version) | CGB | 5/7 | 5/7 | 51% |
| Visiteurs, Les (France) (GB Compatible) | CGB | 5/7 | 5/7 | 78% |
| Wario Land 3 (USA) | CGB | 2/7 | 2/7 | 40% |
| Wario Land II (USA) (SGB Enhanced) | SGB | 0/7 | 0/7 | 29% |
| Wendy - Der Traum von Arizona (Germany) | CGB | 6/7 | 6/7 | 84% |
| Worms (Europe) | DMG | 7/7 | 7/7 | 31% |
| Worms Armageddon (Europe) (En,Fr,De,Es,It,Nl) | CGB | 5/7 | 5/7 | 43% |
| Xtreme Sports (USA) | CGB | 1/7 | 1/7 | 42% |

</details>

## Building

- Install devkitPro's GBA tools (devkitARM). On Windows, use the devkitPro MSYS2 shell.
- Run `make` in this directory. It produces `migoomba.gba` (the emulator) and `migoomba.elf` (the same
  with debug symbols).

### Making a playable ROM

A playable GBA ROM is just the emulator with the Game Boy ROM appended:

    cat migoomba.gba game.gbc > game.gba

`make` does it too, building the emulator first if needed and writing to this directory, and it can
put several games on one cart, with a menu to pick them (what Goomba Front used to build):

    make path/to/game.gbc [OUT=name.gba]               # -> game.gba
    make game1.gbc game2.gb game3.gbc [OUT=name.gba]   # -> compilation.gba, games in this order

Each ROM is padded, or cut, to the size its header declares, since the emulator finds the next game by
that size; the menu shows the names from the ROM headers, and the result must fit in a GBA cart's 32MB.
make splits its arguments at spaces, so rename files with spaces in their names first (or use `cat`
for a single game). Goomba Front's "trimmed" ROMs, which save space by dropping unused banks, are not
made.

For debug symbols in mGBA, put `migoomba.elf` next to the built ROM and rename it to
`(ROM name).elf` (thanks to Endrift for the tip).

The ROM header keeps Goomba's title and game code (`GOOMBA COLOR`, `GMBC`), so kernels and emulator
settings that recognise Goomba builds still do.

## Saves

MiGoomba keeps everything in the GBA save (the flash cart's or emulator's SRAM): saved states and
every game's cart RAM, LZO-packed and found by a checksum of the game's ROM. The running game's cart
RAM is also written through as it changes, so it survives a power-off and is packed on the next boot:

| Game's cart RAM | 64KB GBA SRAM (most flash carts) | 32KB GBA SRAM |
|---|---|---|
| up to 8KB | written through | written through |
| 32KB | written through, if saved states and other saves fit in the first 32KB (with 4KB to spare) | saved when the menu (L+R) opens |

In the last case, open the menu after saving in a 32KB game before switching off. The menu also packs
the save and gives the whole SRAM back to saved states while it is open.

**Flash carts**: the EZ-Flash Omega Definitive Edition keeps saves in FRAM; leave it about ten seconds
after saving before switching off.

**mGBA** gives GBA games 32KB of SRAM unless told otherwise. For 64KB, stop the game (File -> Close
ROM), open Tools -> Game Overrides, set Save type to "SRAM 64kB (bootlegs only)", Save, and load the
ROM again; or, with mGBA closed, add to `~/.config/mgba/config.ini`:

    [override.GMBC]
    savetype=SRAM512

### Moving saves

`tools/goombasave.py` (Python 3, no dependencies) converts a game's save between MiGoomba's GBA save
and a plain Game Boy save: real cartridges (read with a cart dumper), the EverDrive GB X7 and Game Boy
emulators.

    python3 tools/goombasave.py list   GOOMBA.sav [GAME.gbc ...]
    python3 tools/goombasave.py export GOOMBA.sav GAME.gbc GAME.sav
    python3 tools/goombasave.py import GOOMBA.sav GAME.gbc GAME.sav [--delete-states]

It needs the game's ROM to find its save, and keeps `GOOMBA.sav.bak` when importing. Saved states
hold their own copy of cart RAM: loading an older state after an import brings the older save back
(`--delete-states` removes that game's states). MBC3 clock data is not transferred; MiGoomba uses the
GBA's clock.

## Testing

`tools/gbtest` runs MiGoomba headless in libmgba, with exact cycle counts:

- `runtests.py`: the test ROM suites above; `diffruns.py` compares two runs, emulation cost included.
- `gametests.py`: the game checks above, against a baseline of screen hashes in `games.json` and
  against mGBA's Game Boy core (`gbref`).
- `savetest.py`, `goombasave_test.py`: battery saves across power cycles, quick save/load and the
  menu, on 32KB and 64KB of GBA SRAM, and round trips through `goombasave.py`.
- `stackcheck.py`: stack headroom in IWRAM. `profile.py`: where the time goes.

See [tools/gbtest/README.md](tools/gbtest/README.md) for setup and details, and
[AGENTS.md](AGENTS.md) for notes on the code and on how changes are made and measured.

## Thanks

- Jaga (EvilJagaGenius) for [Jagoomba Color](https://github.com/EvilJagaGenius/jagoombacolor), which
  MiGoomba is built on, and its fixes, among them: Donkey Kong Land: New Colors Mode (file select
  menu), Faceball 2000 (menu), Kirby's Dream Land DX Service Repair (level 2 palettes), Konami GB
  Collections 2 and 4 (boot), Metal Gear Solid: Ghost Babel (elevator crash), Pokemon Crystal
  (graphical corruption) and Wario Land DX (boot).
- Dwedit for the Goomba Color emulator: https://www.dwedit.org/gba/goombacolor.php
- FluBBa for the Goomba emulator before that: http://goomba.webpersona.com/
- Minucce for help with ASM and pointing Jaga in the right direction.
- Sterophonick for code tweaks and featuring Jagoomba in the Simple kernel for the EZ-Flash Omega
  carts: https://gbatemp.net/threads/new-theme-for-ez-flash-omega.520665/
- EZ-Flash for releasing the source to their modified Goomba Color builds.
- Nuvie for the code that saves the desired Game Boy type per game.
- Radimerry for the MGS: Ghost Babel elevator fix, Faceball menu fix, and SMLDX SRAM fix.
- Therealteamplayer for the default-to-grayscale code for GB games if no SGB palette is found.
- Endrift for mGBA, which the test tools are built on.

"""Build a kit for checking MiGoomba on real hardware: ROMs to copy to the flash cart, expected results
measured in mGBA, and a checklist to fill in.

usage: hwkit.py --emu migoomba.gba --elf migoomba.elf --out hwtest [--games DIR] [--extra GAME.gbc ...]

  roms/       games built one per file (NN_name.gba), a five-game compilation, and test ROM
              compilations whose menu names are "NN TESTNAME" (Mooneye's ROMs all share one title)
  expected/   per game, MiGoomba's screen in mGBA (top) over mGBA's own Game Boy core (bottom) at the
              7 moments of the input script; per test compilation, the result each entry had in mGBA
  CHECKLIST.md

Commercial games come from --games (gametests' folder) and --extra; the kit must stay out of git.
"""
import argparse, json, os, re, shutil, struct, subprocess, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pngdec import decode

# id, ROM file name pattern, short name, what to check
GAMES = [
    ("01", r"^polishedcrystal", "polished_crystal", "32KB save written through, MBC3 clock, JR wrap (crashed before)"),
    ("02", r"^Pokemon Crystal Version", "pokemon_crystal", "32KB save written through, MBC3 clock"),
    ("03", r"^Super Mario Land 2", "mario_land2", "8KB save written through"),
    ("04", r"^Legend of Zelda, The - Link's Awakening DX", "zelda_dx", "32KB save (MBC5), general CGB"),
    ("05", r"^V-Rally", "v_rally", "per-line DMG palette (race screen)"),
    ("06", r"^Lemmings 2", "lemmings2", "per-line DMG palette"),
    ("07", r"^Rainbow Islands", "rainbow_islands", "per-line CGB palettes (title logo)"),
    ("08", r"^Tomb Raider", "tomb_raider", "per-line CGB palettes; slower than full speed"),
    ("09", r"^Turok 3", "turok3", "per-line palettes, DMA'd maps, display DMA re-latch (was striped)"),
    ("10", r"^Alone in the Dark", "alone_in_the_dark", "timer-driven palette bands (was garbled); right at full speed"),
    ("11", r"^Worms Armageddon", "worms_armageddon", "per-line CGB palettes"),
    ("12", r"^Road Rash", "road_rash", "DMG STAT write interrupt (hung when a race started)"),
    ("13", r"^Robot Poncots", "robopon_sun", "HuC-3 clock port and save RAM (stopped after the Hudson logo)"),
    ("14", r"^Pocket Family GB 2", "pocket_family2", "HuC-3"),
    ("15", r"^Perfect Dark", "perfect_dark", "slowest game in the set (165% of real time in mGBA)"),
    ("16", r"^Harry Potter", "harry_potter", "slower than full speed (134%)"),
    ("17", r"^Kirby's Dream Land 2", "kirby_dl2", "SGB border and palettes"),
    ("18", r"^Shantae", "shantae", "demanding CGB game, general"),
    ("19", r"^Final Fantasy Adventure", "final_fantasy_adventure", "MBC2 save (512 x 4 bits)"),
    ("21", r"^Pokemon Card GB \(", "pokemon_card_gb", "HuC-1 (stopped after its first screens)"),
]
COMPILATION = [r"^Pokemon - Gold Version", r"^Wario Land II", r"^Mega Man Xtreme", r"^Toki Tori", r"^Donkey Kong Land \("]

TESTS = [  # file, (suite, roms subfolder) list, size limit note
    ("30_mooneye_acceptance.gba", "mooneye-test-suite", ["acceptance"]),
    ("31_wilbertpol_acceptance.gba", "mooneye-test-suite-wilbertpol", ["acceptance"]),
    ("32_mooneye_mbc.gba", "mooneye-test-suite", ["emulator-only"]),
    ("33_blargg.gba", "blargg", [""]),
]
GB_X, GB_Y = 40, 8
SCRIPT_HELP = ("After power-on wait about 6 seconds, then press Start, wait 2 s, A, 2 s, A, 2 s, Start, and so on "
               "(Start every third press) for 11 presses, then leave it alone. The expected sheets show the screen "
               "at about 5, 10, 15, 20, 25, 30 and 40 seconds.")


def rom_image(path):
    """A ROM padded or cut to the size its header declares, as the emulator steps from game to game."""
    d = open(path, "rb").read()
    if d[0x104:0x108] != b"\xce\xed\x66\x66":
        raise SystemExit("%s: not a Game Boy ROM" % path)
    size = 0x8000 << d[0x148]
    return d[:size] + bytes(max(0, size - len(d)))


def retitle(rom, title):
    rom = bytearray(rom)
    rom[0x134:0x143] = title.upper().encode("ascii", "replace")[:15].ljust(15, b"\0")
    x = 0
    for b in rom[0x134:0x14D]:
        x = (x - b - 1) & 0xFF
    rom[0x14D] = x
    return bytes(rom)


def write_png(path, rows):
    raw = b"".join(b"\0" + bytes(c for px in row for c in px) for row in rows)
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def crop(path, x0, y0):
    """The 160x144 GB screen at (x0, y0) of a PNG, rows of RGB tuples."""
    _, _, px = decode(path)
    return [list(row[x0:x0 + 160]) for row in px[y0:y0 + 144]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--games", default=os.path.join(HERE, "games"))
    ap.add_argument("--extra", nargs="*", default=[], help="more Game Boy ROMs (e.g. Polished Crystal)")
    a = ap.parse_args()
    emu = open(a.emu, "rb").read()
    roms_dir, exp_dir, tmp = (os.path.join(a.out, d) for d in ("roms", "expected", "tmp"))
    for d in (roms_dir, exp_dir, tmp):
        os.makedirs(d, exist_ok=True)

    files = [os.path.join(a.games, f) for f in sorted(os.listdir(a.games))] + a.extra
    def find(pattern):
        hits = [f for f in files if re.search(pattern, os.path.basename(f))]
        return hits[0] if hits else None

    # games, one per file
    built = []
    for gid, pattern, short, why in GAMES:
        f = find(pattern)
        if not f:
            print("missing: %s" % pattern)
            continue
        out = os.path.join(roms_dir, "%s_%s.gba" % (gid, short))
        open(out, "wb").write(emu + rom_image(f))
        built.append((gid, short, why, f))
    comp = [find(p) for p in COMPILATION]
    comp = [f for f in comp if f]
    open(os.path.join(roms_dir, "20_compilation.gba"), "wb").write(emu + b"".join(rom_image(f) for f in comp))

    # expected screens: gametests on these games (MiGoomba in mGBA, and mGBA's GB core)
    gamedir = os.path.join(tmp, "games")
    os.makedirs(gamedir, exist_ok=True)
    for gid, short, why, f in built:
        shutil.copy(f, os.path.join(gamedir, os.path.basename(f)))
    subprocess.run([sys.executable, os.path.join(HERE, "gametests.py"), "--emu", a.emu, "--elf", a.elf,
                    "--out", os.path.join(tmp, "gt"), "--games", gamedir, "--db", os.path.join(tmp, "none.json")],
                   check=False, stdout=subprocess.DEVNULL)
    results = {r["file"]: r for r in json.load(open(os.path.join(tmp, "gt", "results.json")))}
    for gid, short, why, f in built:
        r = results.get(os.path.basename(f))
        if not r or not r["checkpoints"]:
            continue
        tag = r["sha1"][:10]
        top, bottom = [], []
        for c in r["checkpoints"]:
            g = crop(os.path.join(tmp, "gt", "shots", "%s_%05d_goomba.png" % (tag, c["frame"])), GB_X, GB_Y)
            ref = crop(os.path.join(tmp, "gt", "shots", "%s_%05d_ref.png" % (tag, c["frame"])), 0, 0)
            top.append(g)
            bottom.append(ref)
        gap = [(128, 128, 128)] * 4
        rows = [sum(([*s[y], *gap] for s in top), []) for y in range(144)] + [[(128, 128, 128)] * (164 * len(top))] * 4 + \
               [sum(([*s[y], *gap] for s in bottom), []) for y in range(144)]
        write_png(os.path.join(exp_dir, "%s_%s.png" % (gid, short)), rows)

    # test ROM compilations: expected results from runtests on the same ROMs
    rt = os.path.join(tmp, "rt")
    subprocess.run([sys.executable, os.path.join(HERE, "runtests.py"), "--emu", a.emu, "--elf", a.elf,
                    "--roms", os.path.join(HERE, "roms"), "--out", rt, "--suites", "blargg,mooneye-test-suite,mooneye-test-suite-wilbertpol"],
                   check=False, stdout=subprocess.DEVNULL)
    status = {x["name"]: x["status"] for x in json.load(open(os.path.join(rt, "results.json")))}
    tests = {}
    for out, suite, subdirs in TESTS:
        entries, data = [], []
        for sub in subdirs:
            base = os.path.join(HERE, "roms", suite, sub)
            for root, _, fs in sorted(os.walk(base)):
                for fn in sorted(fs):
                    if fn.endswith(".gb"):
                        name = os.path.relpath(os.path.join(root, fn), os.path.join(HERE, "roms"))
                        if name in status:
                            entries.append(name)
        for i, name in enumerate(entries, 1):
            title = "%02d %s" % (i, os.path.splitext(os.path.basename(name))[0])
            data.append(retitle(rom_image(os.path.join(HERE, "roms", name)), title))
        blob = emu + b"".join(data)
        if len(blob) > 32 << 20:
            print("%s: %d bytes, over 32MB" % (out, len(blob)))
        open(os.path.join(roms_dir, out), "wb").write(blob)
        tests[out] = [(i, name, status[name]) for i, name in enumerate(entries, 1)]
    mbc3 = os.path.join(HERE, "roms", "mbc3-tester", "mbc3-tester.gb")
    if os.path.exists(mbc3):
        open(os.path.join(roms_dir, "34_mbc3_tester.gba"), "wb").write(emu + rom_image(mbc3))
        shutil.copy(os.path.join(HERE, "roms", "mbc3-tester", "mbc3-tester-cgb.png"), os.path.join(exp_dir, "34_mbc3_tester_hardware.png"))
    json.dump(tests, open(os.path.join(exp_dir, "tests.json"), "w"), indent=1)
    write_checklist(a.out, built, comp, tests)
    shutil.rmtree(tmp)
    print("kit in %s" % a.out)


def write_checklist(out, built, comp, tests):
    L = []
    w = L.append
    w("# MiGoomba hardware check\n")
    w("Copy `roms/` to the flash cart. `expected/` holds what mGBA showed: per game, MiGoomba (top row) and "
      "mGBA's own Game Boy core (bottom row) at the moments of the input script; the hardware should look like "
      "the top row. Fill in the Result column (ok / what differs).\n")
    w("Input script for the game sheets: " + SCRIPT_HELP + "\n")
    w("## 1. Basics\n")
    w("| # | Check | Expected | Result |\n|---|---|---|---|")
    w("| 1.1 | Boot `03_mario_land2.gba` | starts the game directly, no menu | |")
    w("| 1.2 | L+R in any game | menu reads \"MiGoomba v0.5 on GBA\", text clean (mGBA's BIOS warned about the font data) | |")
    w("| 1.3 | Boot `20_compilation.gba` | menu lists %d games; each starts | |" % len(comp))
    w("| 1.4 | Sound in a few games | no crackle beyond what you know from Goomba | |\n")
    w("## 2. Saves (most important)\n")
    w("After each save, wait about 10 seconds before switching off (the Omega DE writes its FRAM/SD then).\n")
    w("| # | Check | Expected | Result |\n|---|---|---|---|")
    w("| 2.1 | `01_polished_crystal`: New Game, play to the first save point, save in game, switch off **without** opening the menu, switch on | Continue, with the save | |")
    w("| 2.2 | Copy the cart's save for 2.1 to the computer: `python3 tools/goombasave.py list SAVE.sav polishedcrystal-3.2.3.gbc` | the save is 64KB; PKPCRYSTAL cart RAM listed (live copy newer) | |")
    w("| 2.3 | If 2.1 failed: the save's size | 32KB means the cart gives Goomba only 32KB of SRAM: then only the menu (L+R) saves 32KB games | |")
    w("| 2.4 | `02_pokemon_crystal`: same as 2.1 | Continue | |")
    w("| 2.5 | `03_mario_land2`: clear a level (the game saves), switch off, on | progress kept | |")
    w("| 2.6 | `04_zelda_dx`: save in game (Start+Select+A+B), switch off, on | file kept | |")
    w("| 2.7 | Quick save (R+Select) in Pokemon Crystal, play on, quick load (R+Start) | back to the saved moment | |")
    w("| 2.8 | After 2.7: save in game, switch off, on | the in-game save is there | |")
    w("| 2.9 | `20_compilation`: save in Pokemon Gold, back to the menu (L+R, restart/load another game), play Wario Land II and save, switch off, on: both games | both saves kept | |")
    w("| 2.10 | Round trip: `goombasave.py export` Polished Crystal's save, load it in mGBA (GB core) or on the EverDrive X7 | Continue | |")
    w("| 2.11 | X7 -> MiGoomba: `goombasave.py import` an X7 save of a game you have, copy back, boot | the game sees it | |")
    w("| 2.12 | `19_final_fantasy_adventure` (MBC2): save at an inn, switch off, on | save kept | |")
    w("| 2.13 | `21_pokemon_card_gb` (HuC-1): save in game, switch off, on | Continue | |\n")
    w("## 3. Clock\n")
    w("| # | Check | Expected | Result |\n|---|---|---|---|")
    w("| 3.1 | Pokemon Crystal / Polished Crystal: in-game clock after a few minutes, and after a power cycle | follows real time (the cart's RTC) | |")
    w("| 3.2 | `13_robopon_sun` | title, menu (start / set the clock / GB KISS), game; its clock starts at zero at each power-on (known) | |\n")
    w("## 4. Raster effects and display (timing that mGBA may forgive)\n")
    w("Follow the input script and compare with `expected/NN_name.png`: look for wrong colour bands, flicker, stripes, "
      "missing parts of the screen.\n")
    w("| # | ROM | What to look at | Result |\n|---|---|---|---|")
    for gid, short, why, f in built:
        if gid in ("05", "06", "07", "08", "09", "10", "11", "12", "14", "17", "18"):
            w("| 4.%s | `%s_%s` | %s | |" % (gid, gid, short, why))
    w("| 4.x | Open and close the menu (L+R) a few times during Turok 3's or Tomb Raider's pictures | picture comes back right, no stripes | |\n")
    w("## 5. Speed\n")
    w("| # | ROM | Expected (mGBA) | Result |\n|---|---|---|---|")
    w("| 5.1 | `15_perfect_dark` | visibly slow (165% of a frame) | |")
    w("| 5.2 | `08_tomb_raider`, `16_harry_potter` | somewhat slow (162%, 134%) | |")
    w("| 5.3 | `10_alone_in_the_dark` | full speed or just under (100%) | |")
    w("| 5.4 | Pokemon Crystal walking, Mario Land 2 | full speed | |\n")
    w("## 6. Stability\n")
    w("| # | Check | Expected | Result |\n|---|---|---|---|")
    w("| 6.1 | 20 minutes of Shantae or Zelda DX | no crash, no garbage | |")
    w("| 6.2 | Quick save / load in Turok 3 during a picture | picture right after loading | |\n")
    w("## 7. Test ROMs\n")
    w("Each compilation's menu lists \"NN NAME\". Mooneye: a pass shows the assertions OK and no \"Test failed\"; "
      "Blargg prints its results. Expected is mGBA's result with this build; mark the ones that differ on hardware.\n")
    for rom, entries in tests.items():
        w("### %s (%d tests, %d pass in mGBA)\n" % (rom, len(entries), sum(st == "PASS" for _, _, st in entries)))
        w("| NN | Test | mGBA | Hardware |\n|---|---|---|---|")
        for i, name, st in entries:
            w("| %02d | %s | %s | |" % (i, name.split("/", 1)[1], st))
        w("")
    w("### 34_mbc3_tester.gba\n")
    w("Compare with `expected/34_mbc3_tester_hardware.png` (the test's own picture from real hardware). | |\n")
    open(os.path.join(out, "CHECKLIST.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()

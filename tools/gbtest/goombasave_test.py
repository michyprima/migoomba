"""Round trips through tools/goombasave.py with Goomba itself: export what the game wrote, import a
plain save and check the game reads it, for the savetest ROMs (MBC2; MBC5 with 32KB of RAM, the
counter in bank 3) on 32KB and 64KB of GBA SRAM (32KB cart RAM on 32KB of GBA SRAM: opening the menu before each
power-off, as that is when it is saved).

usage: goombasave_test.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out DIR
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import savetest

TOOL = os.path.join(HERE, "..", "goombasave.py")
COUNTER = {"mbc2": 0, "mbc5_32k": 0x6000}      # where the ROM's counter sits in the plain save


def tool(*args):
    r = subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("goombasave %s: %s" % (args[0], r.stderr.strip() or r.stdout.strip()))
    return r.stdout


def run(emu, elf, out):
    os.makedirs(out, exist_ok=True)
    logo = os.path.join(HERE, "roms", "mooneye-test-suite", "emulator-only", "mbc2", "ram.gb")
    results = {}
    for kind, sram_kb in (("mbc2", 32), ("mbc2", 64), ("mbc5_32k", 64), ("mbc5_32k", 32)):
        name = "%s %dK SRAM" % (kind, sram_kb)
        d = os.path.join(out, "%s_%dk" % (kind, sram_kb))
        os.makedirs(d, exist_ok=True)
        rom = os.path.join(d, "savetest.gb")
        savetest.build_rom(rom, logo, kind)
        gba = os.path.join(d, "savetest.gba")
        open(gba, "wb").write(open(emu, "rb").read() + open(rom, "rb").read())
        sav = os.path.join(d, "goomba.sav")
        if os.path.exists(sav):
            os.remove(sav)
        # 32KB cart RAM with 32KB of GBA SRAM reaches the GBA save only when the menu opens
        menu_only = (kind, sram_kb) == ("mbc5_32k", 32)

        # export: what the game wrote (the live copy, if it still owns it)
        s = savetest.Session(gba, elf, sav, sram_kb)
        seq = [s.report(), s.report(), s.press_a()]          # 0, 1, 2
        if menu_only:
            s.menu()
        s.close()
        plain = os.path.join(d, "exported.sav")
        tool("export", sav, rom, plain)
        exported = open(plain, "rb").read()
        got = exported[COUNTER[kind]] & 0x0F

        # import: a plain save with the counter at 7, over the save the game still owns
        mine = bytearray(len(exported))
        mine[COUNTER[kind]] = 7
        mine_path = os.path.join(d, "mine.sav")
        open(mine_path, "wb").write(mine)
        tool("import", sav, rom, mine_path)
        s = savetest.Session(gba, elf, sav, sram_kb)
        boot = s.report()
        after = s.press_a()                                   # 8: the game keeps saving after an import
        if menu_only:
            s.menu()
        s.close()
        s = savetest.Session(gba, elf, sav, sram_kb)
        boot2 = s.report()
        s.close()

        # import into a GBA save that has never seen the game (and with a clock block, as MBC3 saves have)
        fresh = os.path.join(d, "fresh.sav")
        open(fresh, "wb").write(bytes([0xFF]) * (sram_kb * 1024))
        open(mine_path, "wb").write(bytes(mine) + bytes(48))
        tool("import", fresh, rom, mine_path, "--out", fresh)
        s = savetest.Session(gba, elf, fresh, sram_kb)
        fresh_boot = s.report()
        s.close()

        results[name] = dict(written=seq, exported=got, after_import=[boot, after], next_boot=boot2,
                             fresh=fresh_boot, ok=(seq == [0, 1, 2] and got == 2 and [boot, after] == [7, 8]
                                                   and boot2 == 8 and fresh_boot == 7))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    for name, r in run(a.emu, a.elf, a.out).items():
        print("%-18s %s  %s" % (name, "OK  " if r.pop("ok") else "FAIL", r))


if __name__ == "__main__":
    main()

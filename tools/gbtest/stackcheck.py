"""Check how close Goomba's user stack comes to the data below it.

IWRAM is nearly full and .bss ends just below the user stack. In normal play the stack is deepest in
the VBlank handler (display_frame -> transfer_palette_), which runs in system mode on top of the
emulation loop's stack: about 300 bytes. Below .bss's end sits CHR_DECODE (the tile decode table); if
the stack reaches it, tiles decode wrongly until a canary (CANARY2) triggers a rebuild. Until
2026-11-01 the stack overran it by up to 168 bytes, and 24 more bytes of IWRAM broke cpu_instrs.

Every byte added to IWRAM moves .bss's end up, so run this after any change that adds IWRAM code or
data. It steps every instruction of real workloads and reports the lowest SP and where it happened.

usage: stackcheck.py --emu jagoombacolor.gba --elf jagoombacolor.elf --roms ROMS --out DIR
                     [--game GAME ...] [--min-spare BYTES]
Exit status 1 if the user stack comes within --min-spare bytes (default 32) of the end of .bss, or
the IRQ stack leaves its region.
"""
import argparse, bisect, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, Goomba, symbols, NM


def symbolizer(elf):
    syms = sorted((int(l.split()[0], 16), l.split()[2]) for l in subprocess.run(
        [NM, "-n", elf], capture_output=True, text=True).stdout.splitlines()
        if len(l.split()) == 3 and l.split()[1] in "tTwW")
    addrs = [x[0] for x in syms]
    def name(a):
        i = bisect.bisect_right(addrs, a & ~1) - 1
        return "%s+%x" % (syms[i][1], (a & ~1) - addrs[i]) if i >= 0 else "%08x" % a
    return name
import savetest


def run_rom(emu, elf, rom, gb_frames, out):
    gba = os.path.join(out, os.path.basename(rom) + ".gba")
    open(gba, "wb").write(open(emu, "rb").read() + open(rom, "rb").read())
    h = Harness(gba)
    h.stepall()
    g = Goomba(h, symbols(elf))
    g.boot()
    g.run_gb_frames(gb_frames, absolute=True, max_gba_frames=gb_frames * 3)
    usr, irq = h.stackstats()
    deepest = h.deepest
    h.close()
    return usr, irq, deepest


def run_saves(emu, elf, out):
    """Quick save / quick load and a boot that packs saved cart RAM: Goomba's deepest C paths
    (state compression), with 8KB-style cart RAM and with a live 32KB one (64KB GBA SRAM)."""
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms", "mooneye-test-suite",
                        "emulator-only", "mbc2", "ram.gb")
    best = None
    for kind, sram_kb in (("mbc2", 32), ("mbc5_32k", 64)):
        rom = os.path.join(out, "savetest_%s.gb" % kind)
        savetest.build_rom(rom, logo, kind)
        gba = os.path.join(out, "savetest_%s.gba" % kind)
        open(gba, "wb").write(open(emu, "rb").read() + open(rom, "rb").read())
        sav = os.path.join(out, "stack_%s.sav" % kind)
        if os.path.exists(sav):
            os.remove(sav)
        s = savetest.Session(gba, elf, sav, sram_kb, stepall=True)
        s.report(); s.report()
        s.hotkey(savetest.KEY_SELECT)
        s.press_a()
        s.hotkey(savetest.KEY_START)
        s.press_a()
        usr, irq = s.h.stackstats()
        deepest = s.h.deepest
        s.close()
        s = savetest.Session(gba, elf, sav, sram_kb, stepall=True)   # boot with an existing save: packing/unpacking
        s.report()
        usr2, irq2 = s.h.stackstats()
        if usr2 < usr:
            deepest = s.h.deepest
        s.close()
        r = (min(usr, usr2), min(irq, irq2), deepest)
        if best is None or r[0] < best[0]:
            best = (r[0], min(r[1], best[1]) if best else r[1], r[2])
        elif r[1] < best[1]:
            best = (best[0], r[1], best[2])
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--roms", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", action="append", default=[])
    ap.add_argument("--min-spare", type=int, default=32)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sy = symbols(a.elf)
    bss_end, sp_usr, sp_irq = sy["__bss_end__"], sy["__sp_usr"], sy["__sp_irq"]
    chr_start, chr_end = sy["CHR_DECODE"], sy["CANARY2"]
    print("static: .bss ends %08x, user stack top %08x (%d bytes free), CHR_DECODE %08x-%08x" % (
        bss_end, sp_usr, sp_usr - bss_end, chr_start, chr_end))

    workloads = [("cpu_instrs", lambda: run_rom(a.emu, a.elf, os.path.join(a.roms, "blargg", "cpu_instrs",
                                                                         "cpu_instrs.gb"), 3400, a.out)),
                 ("saves", lambda: run_saves(a.emu, a.elf, a.out))]
    workloads += [(os.path.basename(g), lambda g=g: run_rom(a.emu, a.elf, g, 3600, a.out)) for g in a.game]
    ok = True
    worst_usr, worst_irq = 0xFFFFFFFF, 0xFFFFFFFF
    sym = symbolizer(a.elf)
    for name, fn in workloads:
        usr, irq, (pc, lr) = fn()
        worst_usr, worst_irq = min(worst_usr, usr), min(worst_irq, irq)
        print("  %-28s user stack %3d bytes deep, %4d above .bss end, %2d CHR_DECODE entries exposed"
              " | IRQ stack %2d deep" % (name, sp_usr - usr, usr - bss_end,
                                        max(0, (chr_end - usr + 3) // 4), sp_irq - irq))
        print("  %-28s deepest at %s (lr %s)" % ("", sym(pc), sym(lr)))
    spare = worst_usr - bss_end
    problems = []
    if spare < a.min_spare:
        problems.append("user stack comes within %d bytes of .bss (need %d)" % (spare, a.min_spare))
    if worst_usr < chr_end:
        problems.append("user stack overwrites CHR_DECODE")
    if worst_irq < sp_usr:
        problems.append("IRQ stack runs into the user stack")
    print("%s: user stack worst case %d bytes above .bss end (need %d), IRQ stack %d bytes to spare%s" % (
        "FAIL" if problems else "OK", spare, a.min_spare, worst_irq - sp_usr,
        "".join("\n  - " + p for p in problems)))
    ok = not problems
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

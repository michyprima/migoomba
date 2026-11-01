"""Check how far Goomba's user stack runs into the data below it.

IWRAM is full: .bss ends 144 bytes below the user stack top, and in normal play the stack already goes
deeper than that. The VBlank handler (display_frame -> transfer_palette_) runs in system mode on top
of the emulation loop's stack and reaches about 300 bytes, overwriting the end of CHR_DECODE (the
tile decode table). A canary (CANARY2) above the table makes the next VBlank rebuild it, so the damage
is transient, but entries below the canary can be hit without the canary changing.

Any byte added to IWRAM moves the table up and deepens the overlap: one such change (+24 bytes) broke
cpu_instrs. This runs real workloads with every instruction stepped and reports the lowest SP.

usage: stackcheck.py --emu jagoombacolor.gba --elf jagoombacolor.elf --roms ROMS --out DIR
                     [--game GAME ...] [--max-overlap BYTES]
Exit status 1 if the stack runs more than --max-overlap bytes (default 168, the 2026-11-01 level) past
the end of .bss, or reaches below CHR_DECODE into the palettes, or the IRQ stack leaves its region.
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
    """Quick save / quick load: Goomba's deepest C paths (state compression)."""
    rom = os.path.join(out, "savetest.gb")
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms", "mooneye-test-suite",
                        "emulator-only", "mbc2", "ram.gb")
    savetest.build_rom(rom, logo)
    gba = os.path.join(out, "savetest.gba")
    open(gba, "wb").write(open(emu, "rb").read() + open(rom, "rb").read())
    sav = os.path.join(out, "stack.sav")
    if os.path.exists(sav):
        os.remove(sav)
    s = savetest.Session(gba, elf, sav, stepall=True)
    s.report(); s.report()
    s.hotkey(savetest.KEY_SELECT)
    s.press_a()
    s.hotkey(savetest.KEY_START)
    s.press_a()
    usr, irq = s.h.stackstats()
    deepest = s.h.deepest
    s.close()
    s = savetest.Session(gba, elf, sav, stepall=True)   # boot with an existing save: unpacking path
    s.report()
    usr2, irq2 = s.h.stackstats()
    if usr2 < usr:
        deepest = s.h.deepest
    s.close()
    return min(usr, usr2), min(irq, irq2), deepest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--roms", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game", action="append", default=[])
    ap.add_argument("--max-overlap", type=int, default=168)
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
        print("  %-28s user stack %3d bytes deep, %3d past .bss end, %2d CHR_DECODE entries exposed"
              " | IRQ stack %2d deep" % (name, sp_usr - usr, max(0, bss_end - usr),
                                        max(0, (chr_end - usr + 3) // 4), sp_irq - irq))
        print("  %-28s deepest at %s (lr %s)" % ("", sym(pc), sym(lr)))
    overlap = bss_end - worst_usr
    problems = []
    if overlap > a.max_overlap:
        problems.append("user stack %d bytes past .bss end (allowed %d)" % (overlap, a.max_overlap))
    if worst_usr < chr_start:
        problems.append("user stack reaches below CHR_DECODE (palettes)")
    if worst_irq < sp_usr:
        problems.append("IRQ stack runs into the user stack")
    print("%s: user stack worst case %d bytes past .bss end (allowed %d), IRQ stack %d bytes to spare%s" % (
        "FAIL" if problems else "OK", overlap, a.max_overlap, worst_irq - sp_usr,
        "".join("\n  - " + p for p in problems)))
    ok = not problems
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

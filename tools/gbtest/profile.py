"""Exact cycle profile of Goomba running a GB ROM: every ARM instruction is stepped and timed.

usage: profile.py --emu jagoombacolor.gba --elf jagoombacolor.elf --rom game.gb
                  [--skip GBFRAMES] [--frames GBAFRAMES] [--top N] [--annotate SYMBOL ...]
"""
import argparse, bisect, collections, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, Goomba, symbols, NM

OBJDUMP = NM.replace("-nm", "-objdump")
IDLE_PER_EXEC = 1000   # an instruction averaging more than this per execution is a halt/IntrWait
IDLE_PC = 0xFFFFFFFE   # gbaharness records time spent halted under this PC


def code_symbols(elf):
    out = []
    for l in subprocess.run([NM, "-n", elf], capture_output=True, text=True).stdout.splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "tTwW":
            out.append((int(p[0], 16), p[2]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--skip", type=int, default=0, help="GB frames to run before profiling")
    ap.add_argument("--frames", type=int, default=600, help="GBA frames to profile")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--annotate", nargs="*", default=[])
    a = ap.parse_args()

    tmp = tempfile.mkdtemp()
    gba = os.path.join(tmp, "profile.gba")
    open(gba, "wb").write(open(a.emu, "rb").read() + open(a.rom, "rb").read())
    h = Harness(gba)
    g = Goomba(h, symbols(a.elf))
    g.boot()
    if a.skip:
        g.run_gb_frames(a.skip, absolute=True, max_gba_frames=a.skip * 3)
    f0 = g.gb_frame()
    hist = os.path.join(tmp, "hist.txt")
    h.cmd("profile %d %s" % (a.frames, hist))
    gb_frames = g.gb_frame() - f0
    h.close()

    pcs = {}
    for l in open(hist):
        pc, cyc, cnt = l.split()
        pcs[int(pc, 16)] = (int(cyc), int(cnt))
    idle = sum(c for pc, (c, n) in pcs.items() if pc == IDLE_PC or c / n > IDLE_PER_EXEC)
    busy_pcs = {pc: v for pc, v in pcs.items() if pc != IDLE_PC and v[0] / v[1] <= IDLE_PER_EXEC}
    busy = sum(c for c, _ in busy_pcs.values())

    syms = code_symbols(a.elf)
    addrs = [s[0] for s in syms]

    def sym(pc):
        i = bisect.bisect_right(addrs, pc) - 1
        return syms[i][1] if i >= 0 and addrs[i] >> 24 == pc >> 24 else "(%08x)" % pc

    by = collections.Counter()
    execs = collections.Counter()
    for pc, (c, n) in busy_pcs.items():
        by[sym(pc)] += c
        execs[sym(pc)] += n
    print("%d GBA frames, %d GB frames: busy %d cycles (%.0f per GB frame), idle %d" % (
        a.frames, gb_frames, busy, busy / max(gb_frames, 1), idle))
    print("  %busy    cum%  cyc/instr  symbol")
    acc = 0
    for s, c in by.most_common(a.top):
        acc += c
        print("%6.2f%% %6.2f%%  %8.2f  %s" % (100 * c / busy, 100 * acc / busy, c / execs[s], s))

    for name in a.annotate:
        i = [k for k, s in enumerate(syms) if s[1] == name]
        if not i:
            print("no symbol", name)
            continue
        start, end = syms[i[0]][0], syms[i[0] + 1][0]
        dis = subprocess.run([OBJDUMP, "-d", "--start-address=%#x" % start, "--stop-address=%#x" % end, a.elf],
                             capture_output=True, text=True).stdout
        print("\n== %s" % name)
        for line in dis.splitlines():
            head = line.strip().split(":", 1)
            try:
                pc = int(head[0], 16)
            except ValueError:
                continue
            if len(head) > 1 and "\t" in head[1]:
                c, n = pcs.get(pc, (0, 0))
                print("%6.2f%% %9d execs  %s" % (100 * c / busy if busy else 0, n, line.strip()))


if __name__ == "__main__":
    main()

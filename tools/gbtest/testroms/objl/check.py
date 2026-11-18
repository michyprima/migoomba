"""Runs objl3.gbc/objl4.gbc on mGBA's GB core and on migoomba builds and reports, per sprite segment,
the lines where a build is missing the sprite or shows it where the GB does not.

usage: check.py [--frames N] [--model CGB|DMG] [--shots DIR] BUILD.gba [BUILD.gba ...]
(each BUILD.gba needs its .elf next to it)
"""
import argparse, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from harness import Harness, Goomba, symbols
from gametests import Reference, screen, GB_X, GB_Y
from gen import SPRITES, BARS

FREE_X = 140    # a column no sprite uses: the BG colour of each line


def lines_shown(scr, x):
    """Per line: the colour of an 8-pixel sprite at x, or None where the line shows the BG there."""
    out = []
    for y in range(144):
        bg = scr[y][FREE_X]
        px = scr[y][x:x + 8]
        out.append(px[0] if all(p == px[0] for p in px) and px[0] != bg else None)
    return out


def segments(sprites):
    want = {}
    for i, (x, row, segs) in enumerate(sprites):
        for a, b, label in segs:
            for y in range(a, b + 1):
                want[(i, y)] = label
    return want


def near(i, y, sprites):
    """Describe where an unexpected line sits: the BG bar it covers, or the segment it extends."""
    for a, b, name in BARS:
        if a <= y < b:
            return "over the " + name
    x, row, segs = sprites[i]
    best = min(segs, key=lambda s: min(abs(y - s[0]), abs(y - s[1])))
    return "next to '%s'" % best[2]


def runs(ys):
    out, start = [], None
    for y in sorted(ys) + [None]:
        if start is not None and (y is None or y != prev + 1):
            out.append("%d" % start if prev == start else "%d-%d" % (start, prev))
            start = None
        if y is not None and start is None:
            start = y
        prev = y
    return ", ".join(out)


def report(name, scr, sprites):
    want = segments(sprites)
    colour = None
    bad = 0
    for i, (x, row, segs) in enumerate(sprites):
        shown = lines_shown(scr, x)
        missing = [y for y in range(144) if (i, y) in want and shown[y] is None]
        extra = [y for y in range(144) if (i, y) not in want and shown[y] is not None]
        colours = {shown[y] for y in range(144) if (i, y) in want and shown[y] is not None}
        for a, b, label in segs:
            m = [y for y in missing if a <= y <= b]
            if m:
                print("  %-10s sprite %d '%s': missing lines %s" % (name, i, label, runs(m)))
                bad += len(m)
        groups = {}
        for y in extra:
            groups.setdefault(near(i, y, sprites), []).append(y)
        for where, ys in groups.items():
            print("  %-10s sprite %d extra lines %s (%s)" % (name, i, runs(ys), where))
            bad += len(ys)
        if len(colours) > 1:
            print("  %-10s sprite %d shows %d different colours (wrong tile row?)" % (name, i, len(colours)))
    print("  %-10s %s" % (name, "OK" if not bad else "%d lines differ from the GB" % bad))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--model", default="CGB")
    ap.add_argument("--shots")
    ap.add_argument("builds", nargs="+")
    a = ap.parse_args()
    shots = a.shots or tempfile.mkdtemp()
    os.makedirs(shots, exist_ok=True)
    total = 0
    for n in (3, 4):
        rom = os.path.join(HERE, "objl%d.gbc" % n)
        sprites = SPRITES
        print("objl%d.gbc (%d sprites per line), GB frame %d:" % (n, n, a.frames))
        ref = Reference(rom, a.model)
        ref.run_to(a.frames)
        p = os.path.join(shots, "objl%d_ref.png" % n)
        ref.cmd("shot " + p)
        ref.close()
        # the reference must show exactly the table, or the ROM's timing is off
        if report("reference", screen(p), sprites):
            total += 1
        for build in a.builds:
            tag = os.path.splitext(os.path.basename(build))[0]
            gba = os.path.join(shots, "%s_objl%d.gba" % (tag, n))
            open(gba, "wb").write(open(build, "rb").read() + open(rom, "rb").read())
            syms = symbols(build[:-4] + ".elf")
            h = Harness(gba)
            g = Goomba(h, syms)
            g.boot()
            g.run_gb_frames(a.frames, absolute=True)
            p = os.path.join(shots, "%s_objl%d.png" % (tag, n))
            h.shot(p)
            h.close()
            scr = screen(p, GB_X, GB_Y)
            if n == 4:
                # the 4-sprite build must leave per-line sprites off: then only the end-of-frame OAM
                # shows, where every sprite is hidden
                bad = sum(1 for i, (x, row, segs) in enumerate(sprites) for s in lines_shown(scr, x) if s)
                print("  %-10s %s" % (tag, "OK: no per-line sprites" if not bad else
                                      "%d sprite lines shown: per-line sprites switched on" % bad))
                total += bool(bad)
            else:
                report(tag, scr, sprites)
    print("shots in", shots)


if __name__ == "__main__":
    main()

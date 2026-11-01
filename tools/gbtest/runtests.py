"""Run Game Boy test ROM suites through Goomba (migoomba) on libmgba.

usage: runtests.py --emu migoomba.gba --elf migoomba.elf --roms DIR --out DIR
                   [--suites a,b] [--filter REGEX] [--jobs N]

Result per test: PASS / FAIL / TIMEOUT / ERROR, plus GBA cycles and GB frames used.
"""
import argparse, hashlib, json, os, re, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, Goomba, symbols
from pngdec import decode

GB_X, GB_Y = 40, 8          # where Goomba draws the 160x144 GB screen in the 240x160 frame
FIB = (0x0305, 0x080D, 0x1522)
EXIT_OPCODE = {"mooneye-test-suite-wilbertpol": 0xED}   # default LD B,B (0x40)

BLARGG_TIMED = {            # combined ROMs and emulated seconds needed (howto table, DMG/CGB max)
    "cpu_instrs/cpu_instrs.gb": 55, "instr_timing/instr_timing.gb": 1, "mem_timing/mem_timing.gb": 3,
    "mem_timing-2/mem_timing.gb": 4, "halt_bug.gb": 2, "interrupt_time/interrupt_time.gb": 2,
    "oam_bug/oam_bug.gb": 21, "dmg_sound/dmg_sound.gb": 36, "cgb_sound/cgb_sound.gb": 37,
}
KEYED = {                  # suite -> sub-tests: (name, buttons pressed at the start menu, emulated seconds, reference PNGs)
    "rtc3test": [("basic", ["A"], 13, "rtc3test-basic-tests"), ("range", ["DOWN", "A"], 8, "rtc3test-range-tests"),
                 ("sub-second", ["DOWN", "DOWN", "A"], 26, "rtc3test-sub-second-writes")],
}
GB_KEYS = {"A": 0x01, "B": 0x02, "SELECT": 0x04, "START": 0x08, "RIGHT": 0x10, "LEFT": 0x20, "UP": 0x40, "DOWN": 0x80}
LDBB_SUITES = {             # suite dir -> subdirs to include (None = all)
    "mooneye-test-suite": ["acceptance", "emulator-only", "misc", "madness"],
    "mooneye-test-suite-wilbertpol": ["acceptance", "emulator-only", "misc", "madness"], "same-suite": None, "age-test-roms": None,
    "dmg-acid2": None, "cgb-acid2": None, "mealybug-tearoom-tests": None,
}


def discover(roms, suites, rx, games=(), game_frames=3600, game_shots=6):
    tests = []
    if "blargg" in suites:
        for rel, secs in BLARGG_TIMED.items():
            # margin over the documented runtime: Goomba's timing differences can make tests run longer
            tests.append(dict(suite="blargg", rom=os.path.join(roms, "blargg", rel), kind="timed", seconds=secs * 1.5 + 2))
    for suite, subdirs in LDBB_SUITES.items():
        if suite not in suites:
            continue
        for sub in subdirs or [""]:
            for dp, _, fs in os.walk(os.path.join(roms, suite, sub)):
                for f in sorted(fs):
                    if f.endswith((".gb", ".gbc")):
                        tests.append(dict(suite=suite, rom=os.path.join(dp, f), kind="ldbb",
                                          exit_op=EXIT_OPCODE.get(suite, 0x40)))
    if "mbc3-tester" in suites:         # result on screen after 40 frames (howto)
        tests.append(dict(suite="mbc3-tester", rom=os.path.join(roms, "mbc3-tester", "mbc3-tester.gb"), kind="timed",
                          seconds=2))
    for suite, subtests in KEYED.items():
        if suite in suites:
            rom = os.path.join(roms, suite, suite + ".gb")
            for name, keys, secs, ref in subtests:
                refs = sorted(os.path.join(roms, suite, f) for f in os.listdir(os.path.join(roms, suite))
                              if f.startswith(ref) and f.endswith(".png"))
                tests.append(dict(suite=suite, rom=rom, kind="keyed", keys=keys, seconds=secs * 1.5 + 2,
                                  refs=refs, name="%s/%s.gb#%s" % (suite, suite, name)))
    if "gbmicrotest" in suites:
        for dp, _, fs in os.walk(os.path.join(roms, "gbmicrotest")):
            for f in sorted(fs):
                if f.endswith(".gb"):
                    tests.append(dict(suite="gbmicrotest", rom=os.path.join(dp, f), kind="hram",
                                      seconds=1.0))   # results are read once; a generous window keeps the read point from mattering
    for t in tests:
        t.setdefault("name", os.path.relpath(t["rom"], roms))
    for path in games:
        tests.append(dict(suite="games", rom=path, kind="game", name="games/" + os.path.basename(path),
                          frames=game_frames, shots=game_shots))
    return [t for t in tests if not rx or re.search(rx, t["name"])]


def references(rom):
    """Reference screenshots next to the ROM sharing its base name (e.g. foo-dmg.png, foo_cgb_c.png)."""
    d, base = os.path.dirname(rom), os.path.splitext(os.path.basename(rom))[0]
    if os.path.basename(rom) in ("cpu_instrs.gb", "mem_timing.gb", "instr_timing.gb", "halt_bug.gb",
                                 "interrupt_time.gb", "oam_bug.gb", "dmg_sound.gb", "cgb_sound.gb"):
        return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".png") and f.startswith(base))
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.endswith(".png") and re.fullmatch(re.escape(base) + r"([-_].*)?\.png", f))


def compare(ref_path, shot_path):
    """Pixels that break a consistent 1:1 colour mapping between reference and Goomba's GB screen."""
    _, _, ref = decode(ref_path)
    _, _, act = decode(shot_path)
    fwd, bwd, bad = {}, {}, 0
    for y in range(144):
        for x in range(160):
            r, a = ref[y][x], act[GB_Y + y][GB_X + x]
            if fwd.setdefault(r, a) != a or bwd.setdefault(a, r) != r:
                bad += 1
    return bad


def prepare(emu, rom, cache):
    data = open(emu, "rb").read() + open(rom, "rb").read()
    path = os.path.join(cache, hashlib.sha1(data).hexdigest()[:16] + ".gba")
    if not os.path.exists(path):
        open(path, "wb").write(data)
    return path


def run_one(t, emu, elf, out, cache):
    res = dict(name=t["name"], suite=t["suite"], status="ERROR", detail="")
    h = None
    try:
        size = os.path.getsize(t["rom"])
        if size < 0x8000 and t["kind"] != "game":
            res.update(status="SKIP", detail="ROM smaller than 32KB")
            return res
        h = Harness(prepare(emu, t["rom"], cache))
        g = Goomba(h, symbols(elf))
        # Normal (throttled) mode, watched from before the first GB instruction.
        # All execution after boot is stepped, so idle time (halt / vblank waits) is excluded.
        g.boot()
        h.stats()
        f0 = g.gb_frame()
        shot = os.path.join(out, "shots", re.sub(r"[^\w.-]", "_", t["name"]) + ".png")
        os.makedirs(os.path.dirname(shot), exist_ok=True)
        if t["kind"] == "game":
            # real-game workload: busy cycles per GB frame, screenshots at checkpoints for review
            shots = []
            for k in range(1, t["shots"] + 1):
                g.run_gb_frames(t["frames"] * k // t["shots"], max_gba_frames=t["frames"] * 3, absolute=True)
                p = shot[:-4] + "_%05d.png" % g.gb_frame()
                h.shot(p)
                shots.append(p)
            total, idle = h.stats()
            res.update(status="RAN", gba_cycles=total - idle, gba_idle=idle, gb_frames=g.gb_frame() - f0,
                       shots=shots, detail="%.0f busy cycles per GB frame" % ((total - idle) / (g.gb_frame() - f0)))
            return res
        if t["kind"] == "keyed":
            # pick the sub-test at the start menu, then let it run
            g.run_gb_frames(60, absolute=True)
            for k in t["keys"]:
                h.cmd("keys %x" % GB_KEYS[k])
                h.frames(6)
                h.cmd("keys 0")
                h.frames(20)
            f1 = g.gb_frame()
            if not g.run_gb_frames(f1 + int(t["seconds"] * 59.73), step=30,
                                   max_gba_frames=int(t["seconds"] * 60 * 2) + 120, absolute=True):
                res.update(status="TIMEOUT", detail="GB frames did not advance")
        elif t["kind"] == "timed" or t["kind"] == "hram":
            done = g.run_gb_frames(max(2, int(t["seconds"] * 59.73) + 2), step=10 if t["kind"] == "hram" else 30,
                                   max_gba_frames=int(t["seconds"] * 60 * 2) + 120, absolute=True)
            if not done:
                res.update(status="TIMEOUT", detail="GB frames did not advance")
        else:
            op = t["exit_op"]
            hit = h.until(g.opcode_handler(op), 1800, cond=(9, -1, op))  # gb_pc (r9) is past the opcode
            if not hit:
                res.update(status="TIMEOUT", detail="no exit opcode %02X within 1800 GBA frames" % op)
            else:
                r = h.regs()
                res["regs"] = "BC=%04x DE=%04x HL=%04x" % (r[5] >> 16, r[6] >> 16, r[7] >> 16)
        total, idle = h.stats()
        res.update(gba_cycles=total - idle, gba_idle=idle, gb_frames=g.gb_frame() - f0)
        if res["status"] == "TIMEOUT":
            h.shot(shot)
            res["shot"] = shot
            return res
        refs = t.get("refs") or references(t["rom"])
        if t["kind"] == "hram":
            v = g.gb_read(0xFF80, 3)
            res.update(status={0x01: "PASS", 0xFF: "FAIL"}.get(v[2], "NORESULT"), detail="FF80-82=%s" % v.hex())
        elif refs:
            g.run_gb_frames(4, step=1, max_gba_frames=60)   # let Goomba present the final frame
            h.shot(shot)
            res["shot"] = shot
            scores = {os.path.basename(p): compare(p, shot) for p in refs}
            best = min(scores, key=scores.get)
            res.update(status="PASS" if scores[best] == 0 else "FAIL",
                       detail="best ref %s: %d px differ" % (best, scores[best]))
        elif t["kind"] == "ldbb":
            r = h.regs()
            ok = (r[5] >> 16, r[6] >> 16, r[7] >> 16) == FIB
            res.update(status="PASS" if ok else "FAIL", detail=res["regs"])
        else:
            res.update(status="ERROR", detail="no reference screenshot")
    except Exception as e:
        res.update(status="ERROR", detail="%s: %s" % (type(e).__name__, e))
        res["trace"] = traceback.format_exc(limit=3)
    finally:
        if h:
            h.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--roms", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--suites", default="blargg,gbmicrotest,mbc3-tester," + ",".join(KEYED) + "," + ",".join(LDBB_SUITES))
    ap.add_argument("--filter")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--game", action="append", default=[], help="real game ROM to run as a workload (repeatable)")
    ap.add_argument("--game-frames", type=int, default=3600)
    ap.add_argument("--game-shots", type=int, default=6)
    a = ap.parse_args()
    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)
    tests = discover(a.roms, [s for s in a.suites.split(",") if s], a.filter, a.game, a.game_frames, a.game_shots)
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_one, t, a.emu, a.elf, a.out, cache) for t in tests]
        for f in as_completed(futs):
            results.append(f.result())
    results.sort(key=lambda r: r["name"])
    json.dump(results, open(os.path.join(a.out, "results.json"), "w"), indent=1)
    by = {}
    for r in results:
        by.setdefault(r["suite"], {}).setdefault(r["status"], 0)
        by[r["suite"]][r["status"]] += 1
    print("%d tests in %.1fs" % (len(results), time.time() - t0))
    for s, c in sorted(by.items()):
        total = sum(c.values())
        print("  %-32s %4d/%-4d pass   %s" % (s, c.get("PASS", 0), total,
              " ".join("%s=%d" % kv for kv in sorted(c.items()) if kv[0] != "PASS")))


if __name__ == "__main__":
    main()

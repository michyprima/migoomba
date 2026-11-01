"""Real games as tests: Goomba against its own recorded baseline, and against mGBA's GB core as a reference.

Commercial ROMs are never committed: put them (any file name) in a gitignored folder, by default
tools/gbtest/games/. Games are identified by the SHA-1 of the ROM file. The database (games.json, committed)
stores per game: title, input script, the model Goomba ran it on, and per checkpoint the hash of Goomba's
GB screen, the hash of the reference screen and how far Goomba was from the reference.

Each game runs the same input script in both emulators and is captured at the same GB frames:
  - Goomba (gbaharness): screen hashes must equal the recorded ones, otherwise the game is CHANGED
    (a regression, or an intended change to accept with --record). A game whose GB frames stop
    advancing is HANG.
  - reference (gbref, mGBA's GB core on the same model; AGB when Goomba identifies as a GBA): informational. "match" counts checkpoints where
    Goomba's screen equals the reference up to recolouring (Goomba uses its own palettes); input timing
    differs by a frame or so between the two, so a mismatch is a lead to look at, not a failure.

usage: gametests.py --emu migoomba.gba --elf migoomba.elf --out DIR
                    [--games DIR] [--db games.json] [--filter REGEX] [--record] [--jobs N]
"""
import argparse, hashlib, json, os, re, subprocess, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, Goomba, symbols, HERE
from pngdec import decode

GB_X, GB_Y = 40, 8          # where Goomba draws the 160x144 GB screen in the 240x160 frame
KEYS = {"A": 0x01, "B": 0x02, "SELECT": 0x04, "START": 0x08}

# Generic script: let the intro play, then alternate Start and A to get through title screens and menus,
# then leave the game alone (gameplay idling or attract mode). Events: [GB frame, action, arg].
SCRIPTS = {
    "start-a": dict(
        frames=2400,
        hold=6,
        events=[[f, "shot", None] for f in (300, 600, 900, 1200, 1500, 1800, 2400)] +
               [[360 + 120 * i, "press", "START" if i % 3 == 0 else "A"] for i in range(11)],
    ),
}


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Reference:
    """Driver for gbref (mGBA GB core), same line protocol as gbaharness."""

    def __init__(self, rom, model):
        self.p = subprocess.Popen([os.path.join(HERE, "build", "gbref"), rom, model], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def cmd(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        out = []
        while True:
            l = self.p.stdout.readline()
            if not l:
                raise RuntimeError("gbref died during %r" % line)
            l = l.rstrip("\n")
            if l == "ok":
                return out
            if l.startswith("error"):
                raise RuntimeError("gbref: %s" % l)
            out.append(l)

    def frame(self): return int(self.cmd("frame")[0])

    def run_to(self, frame):
        n = frame - self.frame()
        if n > 0:
            self.cmd("frames %d" % n)

    def close(self):
        try:
            self.cmd("quit")
        except Exception:
            pass
        self.p.wait(timeout=10)


def screen(path, x0=0, y0=0):
    """The 160x144 GB screen of a PNG as a list of rows of RGB tuples."""
    _, _, px = decode(path)
    return [row[x0:x0 + 160] for row in px[y0:y0 + 144]]


def screen_hash(rows):
    h = hashlib.sha1()
    for row in rows:
        h.update(bytes(c for p in row for c in p))
    return h.hexdigest()


def recolour_diff(a, b):
    """Pixels that break a consistent 1:1 colour mapping between two screens."""
    fwd, bwd, bad = {}, {}, 0
    for ra, rb in zip(a, b):
        for pa, pb in zip(ra, rb):
            if fwd.setdefault(pa, pb) != pb or bwd.setdefault(pb, pa) != pa:
                bad += 1
    return bad


def schedule(script):
    """Events in time order, key releases included; shots come before presses on the same frame."""
    ev = []
    for f, action, arg in script["events"]:
        ev.append((f, 0 if action == "shot" else 1, action, arg))
        if action == "press":
            ev.append((f + script["hold"], 2, "release", None))
    return sorted(ev, key=lambda e: (e[0], e[1]))


def run_game(rom, sha, entry, emu, elf, out, cache):
    script_name = (entry or {}).get("script", "start-a")
    script = SCRIPTS[script_name]
    res = dict(sha1=sha, file=os.path.basename(rom), title=(entry or {}).get("title") or os.path.splitext(os.path.basename(rom))[0],
               script=script_name, status="ERROR", checkpoints=[])
    h = ref = None
    try:
        data = open(emu, "rb").read() + open(rom, "rb").read()
        gba = os.path.join(cache, hashlib.sha1(data).hexdigest()[:16] + ".gba")
        if not os.path.exists(gba):
            open(gba, "wb").write(data)
        syms = symbols(elf)
        h = Harness(gba)
        g = Goomba(h, syms)
        g.boot()
        h.stats()
        f0 = g.gb_frame()
        shots = os.path.join(out, "shots")
        os.makedirs(shots, exist_ok=True)
        tag = sha[:10]
        ref_model = None
        keys = 0
        hang = False
        for f, _, action, arg in schedule(script):
            if not g.run_gb_frames(f, max_gba_frames=f * 3 + 300, absolute=True):
                hang = True
                break
            if ref_model is None:
                gbc, sgb = h.read(syms["_gbcmode"], 2)
                gba = h.read(syms["_gbamode"], 1)[0]   # "Identify as GBA" (off by default)
                ref_model = ("AGB" if gba else "CGB") if gbc else "SGB" if sgb else "DMG"
                ref = Reference(rom, ref_model)
            ref.run_to(f)
            if action == "press":
                keys |= KEYS[arg]
            elif action == "release":
                keys = 0
            if action in ("press", "release"):
                h.cmd("keys %x" % keys)
                ref.cmd("keys %x" % keys)
                continue
            gp = os.path.join(shots, "%s_%05d_goomba.png" % (tag, f))
            rp = os.path.join(shots, "%s_%05d_ref.png" % (tag, f))
            h.shot(gp)
            ref.cmd("shot %s" % rp)
            gs, rs = screen(gp, GB_X, GB_Y), screen(rp)
            res["checkpoints"].append(dict(frame=f, goomba_frame=g.gb_frame(), goomba=screen_hash(gs),
                                           reference=screen_hash(rs), ref_diff=recolour_diff(rs, gs),
                                           colours=len({p for row in gs for p in row})))
        total, idle = h.stats()
        frames = g.gb_frame() - f0
        res.update(model=ref_model, gb_frames=frames,
                   busy_per_frame=round((total - idle) / frames) if frames else None,
                   status="HANG" if hang else "RAN")
    except Exception as e:
        res.update(status="ERROR", detail="%s: %s" % (type(e).__name__, e), trace=traceback.format_exc(limit=3))
    finally:
        for p in (h, ref):
            if p:
                p.close()
    return res


def judge(res, entry):
    """Compare a run with its database entry; returns (status, notes)."""
    if res["status"] in ("ERROR", "HANG"):
        return res["status"], [res.get("detail", "GB frames stopped advancing")]
    if not entry:
        return "NEW", ["not in the database (add with --record)"]
    notes = []
    old = {c["frame"]: c for c in entry.get("checkpoints", [])}
    changed = [c["frame"] for c in res["checkpoints"] if old.get(c["frame"], {}).get("goomba") != c["goomba"]]
    if entry.get("model") != res.get("model"):
        notes.append("model %s -> %s" % (entry.get("model"), res.get("model")))
    if changed:
        notes.append("screen changed at GB frame %s" % ", ".join(map(str, changed)))
    refchg = [c["frame"] for c in res["checkpoints"] if old.get(c["frame"], {}).get("reference") != c["reference"]]
    if refchg:
        notes.append("reference changed at %s (gbref/script changed?)" % ", ".join(map(str, refchg)))
    om = sum(1 for c in entry.get("checkpoints", []) if c["ref_diff"] == 0)
    nm = sum(1 for c in res["checkpoints"] if c["ref_diff"] == 0)
    if nm != om:
        notes.append("reference matches %d -> %d" % (om, nm))
    if entry.get("busy_per_frame") and res.get("busy_per_frame"):
        d = 100.0 * (res["busy_per_frame"] - entry["busy_per_frame"]) / entry["busy_per_frame"]
        if abs(d) >= 0.5:
            notes.append("busy cycles %+.1f%%" % d)
    return ("CHANGED" if changed or notes and notes[0].startswith("model") else "PASS"), notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--games", default=os.path.join(HERE, "games"))
    ap.add_argument("--db", default=os.path.join(HERE, "games.json"))
    ap.add_argument("--filter", help="regex on title or file name")
    ap.add_argument("--record", action="store_true", help="store these results as the new expectations")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()

    db = json.load(open(a.db)) if os.path.exists(a.db) else {"games": {}}
    roms = []
    for f in sorted(os.listdir(a.games)) if os.path.isdir(a.games) else []:
        p = os.path.join(a.games, f)
        if os.path.isfile(p) and f.lower().endswith((".gb", ".gbc")):
            sha = sha1_file(p)
            entry = db["games"].get(sha)
            if a.filter and not re.search(a.filter, f + " " + ((entry or {}).get("title") or ""), re.I):
                continue
            roms.append((p, sha, entry))
    missing = [e["title"] for s, e in db["games"].items() if s not in {r[1] for r in roms}] if not a.filter else []

    cache = os.path.join(a.out, "cache")
    os.makedirs(cache, exist_ok=True)
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_game, p, sha, entry, a.emu, a.elf, a.out, cache) for p, sha, entry in roms]
        for fu in as_completed(futs):
            results.append(fu.result())
    results.sort(key=lambda r: r["title"].lower())

    counts = {}
    print("%d games in %.0fs" % (len(results), time.time() - t0))
    print("%-8s %-44s %-4s %8s %6s  %s" % ("status", "game", "mode", "busy/fr", "ref", "notes"))
    for r in results:
        status, notes = judge(r, db["games"].get(r["sha1"]))
        r["judged"], r["notes"] = status, notes
        counts[status] = counts.get(status, 0) + 1
        cps = r["checkpoints"]
        print("%-8s %-44s %-4s %8s %6s  %s" % (status, r["title"][:44], r.get("model") or "-",
              r.get("busy_per_frame") or "-", "%d/%d" % (sum(c["ref_diff"] == 0 for c in cps), len(cps)),
              "; ".join(notes)))
    print("  " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    for t in missing:
        print("  not found in %s: %s" % (a.games, t))
    json.dump(results, open(os.path.join(a.out, "results.json"), "w"), indent=1)

    if a.record:
        for r in results:
            if r["status"] != "RAN":
                print("  not recorded (%s): %s" % (r["status"], r["title"]))
                continue
            old = db["games"].get(r["sha1"], {})
            db["games"][r["sha1"]] = dict(title=old.get("title") or r["title"], file=old.get("file") or r["file"],
                                          script=r["script"], model=r["model"], busy_per_frame=r["busy_per_frame"],
                                          checkpoints=[{k: c[k] for k in ("frame", "goomba", "reference", "ref_diff")}
                                                       for c in r["checkpoints"]])
        db["games"] = dict(sorted(db["games"].items(), key=lambda kv: kv[1]["title"].lower()))
        with open(a.db, "w") as f:
            json.dump(db, f, indent=1)
            f.write("\n")
        print("recorded %s" % a.db)
    sys.exit(0 if all(r["judged"] in ("PASS",) for r in results) or a.record else 1)


if __name__ == "__main__":
    main()

"""Compare two runtests.py result sets: status changes and GBA-cycle deltas (tests are deterministic).
gba_cycles are busy cycles (idle waits excluded).
usage: diffruns.py BASE/results.json NEW/results.json"""
import json, sys

base = {r["name"]: r for r in json.load(open(sys.argv[1]))}
new = {r["name"]: r for r in json.load(open(sys.argv[2]))}
changed = [(n, base[n]["status"], new[n]["status"]) for n in sorted(base.keys() & new.keys())
           if base[n]["status"] != new[n]["status"]]
print("status changes: %d" % len(changed))
for n, a, b in changed:
    print("  %-8s -> %-8s %s" % (a, b, n))
# Busy cycles per GB frame. Goomba's start-up (the ROM menu fade calls run(0), which restarts the
# GB frame) can shift a test by a GB frame or two between builds, so totals are not compared directly.
MIN_FRAMES = 60
rows = []
for n in sorted(base.keys() & new.keys()):
    a, b = base[n], new[n]
    if a["status"] == b["status"] and min(a.get("gb_frames") or 0, b.get("gb_frames") or 0) >= MIN_FRAMES:
        rows.append((n, a["gba_cycles"] / a["gb_frames"], b["gba_cycles"] / b["gb_frames"], a["gb_frames"]))
if rows:
    ta = sum(r[1] * r[3] for r in rows)
    tb = sum(r[2] * r[3] for r in rows)
    print("busy cycles per GB frame over %d tests (>= %d GB frames, weighted by frames): %+.2f%%" % (
        len(rows), MIN_FRAMES, 100 * (tb - ta) / ta))
    rows.sort(key=lambda r: (r[2] - r[1]) / r[1])
    for n, a, b, _ in rows[:5] + rows[-5:]:
        print("  %+7.2f%%  %8.0f -> %8.0f  %s" % (100 * (b - a) / a, a, b, n))

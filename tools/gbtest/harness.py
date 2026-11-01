"""Python driver for gbaharness (libmgba) plus Goomba-specific helpers."""
import os, subprocess, struct

HERE = os.path.dirname(os.path.abspath(__file__))
NM = "/opt/devkitpro/devkitARM/bin/arm-none-eabi-nm"


def symbols(elf):
    out = {}
    for l in subprocess.run([NM, elf], capture_output=True, text=True).stdout.splitlines():
        p = l.split()
        if len(p) == 3:
            out[p[2]] = int(p[0], 16)
    return out


class Harness:
    def __init__(self, gba_path, stderr=subprocess.DEVNULL):
        self.p = subprocess.Popen([os.path.join(HERE, "build", "gbaharness"), gba_path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)

    def cmd(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        out = []
        while True:
            l = self.p.stdout.readline()
            if not l:
                raise RuntimeError("harness died during %r" % line)
            l = l.rstrip("\n")
            if l == "ok":
                return out
            out.append(l)

    def frames(self, n): self.cmd("frames %d" % n)
    def stepframes(self, n): self.cmd("stepframes %d" % n)
    def step(self, n=1): self.cmd("step %d" % n)

    def stats(self):
        """(total, idle) GBA cycles of stepped execution since the last call."""
        total, idle = map(int, self.cmd("stats")[0].split())
        return total, idle
    def cycles(self): return int(self.cmd("cycles")[0])
    def frame(self): return int(self.cmd("frame")[0])
    def read(self, addr, n): return bytes.fromhex(self.cmd("r %x %d" % (addr, n))[0])
    def write(self, addr, data): self.cmd("w %x %s" % (addr, data.hex()))
    def u32(self, addr): return struct.unpack("<I", self.read(addr, 4))[0]
    def shot(self, path): self.cmd("shot %s" % path)
    def until(self, addr, maxframes, cond=None):
        """cond = (reg, delta, byte): also require byte at gprs[reg]+delta == byte."""
        extra = " %d %d %x" % cond if cond else ""
        return self.cmd("until %x %d%s" % (addr, maxframes, extra))[0] == "hit"
    def regs(self): return [int(x, 16) for x in self.cmd("regs")[0].split()]

    def close(self):
        try:
            self.cmd("quit")
        except Exception:
            pass
        self.p.wait(timeout=10)


class Goomba:
    """Goomba-aware view: GB frame counter, GB memory via memmap_tbl, unthrottled mode."""
    OFF_FRAME, OFF_MEMMAP = 1204, 1088

    def __init__(self, h, syms):
        self.h, self.s = h, syms
        self.g = syms["GLOBAL_PTR_BASE"]

    def boot(self, max_frames=300):
        """Run until Goomba's opcode table is set up, before any GB code has executed.
        (GB code already runs during the ROM menu fade-in via run(0), so waiting for the
        final run(1) can miss tests that finish within their first few frames.)"""
        for _ in range(max_frames):
            if self.opcode_handler(0x40) and self.gb_frame() == 0:
                return
            self.h.frames(1)
        raise RuntimeError("Goomba opcode table never initialised")

    def started(self):
        return self.h.u32(self.g + self.OFF_MEMMAP) != 0

    def unthrottle(self):
        """Goomba's fast mode. Only sticks once run(1) has started (run() clears it).
        Test runs use normal mode; whether fast mode changes GB-visible behaviour is unverified."""
        self.h.write(self.s["novblankwait"], b"\x01")
    def gb_frame(self): return self.h.u32(self.g + self.OFF_FRAME)
    def opcode_handler(self, op): return self.h.u32(self.g + op * 4)

    def gb_read(self, addr, n):
        base = self.h.u32(self.g + self.OFF_MEMMAP + (addr >> 12) * 4)
        return self.h.read((base + addr) & 0xffffffff, n)

    def run_gb_frames(self, n, step=30, max_gba_frames=None, absolute=False):
        target = n if absolute else self.gb_frame() + n
        start = self.h.frame()
        while self.gb_frame() < target:
            if max_gba_frames is not None and self.h.frame() - start > max_gba_frames:
                return False
            self.h.stepframes(step)
        return True

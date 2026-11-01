"""Battery-save round trips through Goomba's SRAM handling, using a small generated MBC2 ROM.

The ROM keeps a counter in cart RAM at $A000. At boot it reports the stored value, writes 1 if
it was 0, and increments it on each A press; every report puts the value in D and runs LD B,B.
Each harness run is one power-on; the GBA battery save (.sav) is kept between runs.

usage: savetest.py --emu jagoombacolor.gba --elf jagoombacolor.elf --out DIR
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Harness, Goomba, symbols

KEY_A, KEY_SELECT, KEY_START, KEY_R = 0x001, 0x004, 0x008, 0x100


def build_rom(path, logo_source):
    """Hand-assembled MBC2 + battery ROM (cart type $06)."""
    code, labels, fixups = bytearray(), {}, []

    def emit(*b): code.extend(b)
    def label(n): labels[n] = 0x150 + len(code)
    def jr(op, target): emit(op, 0); fixups.append(("jr", len(code) - 1, target))
    def call(target): emit(0xCD, 0, 0); fixups.append(("abs", len(code) - 2, target))

    emit(0xF3)                         # di
    emit(0x31, 0xFE, 0xFF)             # ld sp,$FFFE
    emit(0x3E, 0x0A, 0xEA, 0x00, 0x00) # ld a,$0A / ld ($0000),a   enable cart RAM
    call("report")
    emit(0x7A, 0xB7)                   # ld a,d / or a
    jr(0x20, "loop")                   # jr nz,loop
    emit(0x3E, 0x01, 0xEA, 0x00, 0xA0) # ld a,1 / ld ($A000),a
    call("report")
    label("loop")
    emit(0x3E, 0x10, 0xE0, 0x00)       # ld a,$10 / ldh ($00),a   select buttons
    emit(0xF0, 0x00, 0xCB, 0x47)       # ldh a,($00) / bit 0,a     A pressed = 0
    jr(0x20, "loop")                   # jr nz,loop
    emit(0xFA, 0x00, 0xA0, 0x3C, 0xE6, 0x0F, 0xEA, 0x00, 0xA0)  # ld a,($A000) / inc a / and $0F / ld ($A000),a
    call("report")
    label("release")
    emit(0xF0, 0x00, 0xCB, 0x47)       # ldh a,($00) / bit 0,a
    jr(0x28, "release")                # jr z,release
    jr(0x18, "loop")                   # jr loop
    label("report")
    emit(0xFA, 0x00, 0xA0, 0xE6, 0x0F, 0x57, 0x40, 0xC9)  # ld a,($A000) / and $0F / ld d,a / ld b,b / ret

    for kind, at, target in fixups:
        dest = labels[target]
        if kind == "jr":
            off = dest - (0x150 + at + 1)
            assert -128 <= off <= 127
            code[at] = off & 0xFF
        else:
            code[at], code[at + 1] = dest & 0xFF, dest >> 8

    rom = bytearray(0x8000)
    rom[0x100:0x104] = b"\x00\xC3\x50\x01"                # nop / jp $0150
    rom[0x104:0x134] = open(logo_source, "rb").read()[0x104:0x134]
    rom[0x134:0x13C] = b"SAVETEST"
    rom[0x147], rom[0x148], rom[0x149] = 0x06, 0x00, 0x00  # MBC2 + battery, 32kB ROM
    rom[0x14D] = (-sum(rom[0x134:0x14D]) - 25) & 0xFF      # header checksum
    rom[0x150:0x150 + len(code)] = code
    open(path, "wb").write(rom)


class Session:
    """One power-on of the GBA with the given battery save."""

    def __init__(self, gba, elf, sav):
        self.h = Harness(gba, save=sav)
        self.g = Goomba(self.h, symbols(elf))
        self.g.boot()

    def report(self, maxframes=600):
        if not self.h.until(self.g.opcode_handler(0x40), maxframes, cond=(9, -1, 0x40)):
            return None
        d = (self.h.regs()[6] >> 24) & 0xFF
        self.h.step(1)
        return d

    def press(self, keys, hold=6, after=20):
        self.h.cmd("keys %x" % keys)
        self.h.frames(hold)
        self.h.cmd("keys 0")
        self.h.frames(after)

    def press_a(self):
        """Press A and catch the report it triggers (the ROM reports while the key is still held).
        Waits first: the ROM's boot reports happen during Goomba's menu fade-in, whose UI would
        swallow the press."""
        self.h.frames(60)
        self.h.cmd("keys %x" % KEY_A)
        d = self.report(120)
        self.h.cmd("keys 0")
        self.h.frames(20)
        return d

    def hotkey(self, second, shot=None):
        """Goomba hotkeys: hold R, then press SELECT (quick save) or START (quick load)."""
        self.h.frames(60)
        self.h.cmd("keys %x" % KEY_R)
        self.h.frames(4)
        self.press(KEY_R | second, hold=4, after=0)
        self.h.cmd("keys 0")
        self.h.frames(120)
        if shot:
            self.h.shot(shot)

    def close(self):
        self.h.frames(30)
        self.h.close()


def run(emu, elf, out):
    os.makedirs(out, exist_ok=True)
    rom = os.path.join(out, "savetest.gb")
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms", "mooneye-test-suite",
                        "emulator-only", "mbc2", "ram.gb")
    build_rom(rom, logo)
    gba = os.path.join(out, "savetest.gba")
    open(gba, "wb").write(open(emu, "rb").read() + open(rom, "rb").read())
    results = {}

    # 1. power cycles: value written in one session must be there in the next
    sav = os.path.join(out, "powercycle.sav")
    if os.path.exists(sav):
        os.remove(sav)
    s = Session(gba, elf, sav)
    first = [s.report(), s.report()]           # boot value (0), after writing 1
    first.append(s.press_a())                  # 2
    s.close()
    s = Session(gba, elf, sav)
    second = s.report()
    second_after = s.press_a()
    s.close()
    s = Session(gba, elf, sav)
    third = s.report()
    s.close()
    results["power cycle"] = dict(session1=first, session2_boot=second, session2_after_A=second_after,
                                  session3_boot=third, ok=(first == [0, 1, 2] and second == 2 and third == 3))

    # 2. quick save / quick load, then a write, then a power cycle
    sav = os.path.join(out, "savestate.sav")
    if os.path.exists(sav):
        os.remove(sav)
    s = Session(gba, elf, sav)
    seq = [s.report(), s.report()]             # 0, 1
    s.hotkey(KEY_SELECT, os.path.join(out, "after_quicksave.png"))   # quick save with the value at 1
    seq.append(s.press_a())                    # 2
    s.hotkey(KEY_START, os.path.join(out, "after_quickload.png"))    # quick load: cart RAM should be back to 1
    after_load = s.press_a()                   # 2 if the savestate restored cart RAM, 3 if not
    s.close()
    s = Session(gba, elf, sav)
    boot = s.report()                          # must equal the last value written
    s.close()
    results["savestate"] = dict(session1=seq, after_quickload_and_A=after_load, next_boot=boot,
                                restored_ram=after_load == 2, persisted=boot == after_load,
                                ok=(after_load == 2 and boot == after_load))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", required=True)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    for name, r in run(a.emu, a.elf, a.out).items():
        print("%-12s %s  %s" % (name, "OK  " if r.pop("ok") else "FAIL", r))


if __name__ == "__main__":
    main()

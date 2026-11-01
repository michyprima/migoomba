#!/usr/bin/env python3
"""Move Game Boy battery saves in and out of Goomba Color's GBA save.

Goomba keeps every game's cart RAM in the GBA save (the .sav of the GBA flash cart or emulator,
32KB or 64KB), LZO-packed and identified by a checksum of the game's ROM. Other places keep a Game
Boy save as plain cart RAM: real cartridges (dumped with GBxCart RW, Joey Jr, ...), the EverDrive
GB X7 and Game Boy emulators. This tool converts between the two.

  goombasave.py list   GOOMBA.sav [ROM.gb ...]          what the GBA save holds
  goombasave.py export GOOMBA.sav ROM.gb OUT.sav        a game's cart RAM as a plain save
  goombasave.py import GOOMBA.sav ROM.gb IN.sav [--delete-states] [--out NEW.sav]
                                                       a plain save into the GBA save

ROM.gb is the Game Boy ROM (Goomba finds a game's save by a checksum of its first 16KB). import
rewrites GOOMBA.sav in place unless --out is given; the original is kept as GOOMBA.sav.bak.
Goomba's saved states hold their own copy of cart RAM: loading an older state brings the older save
back, so import warns about them (--delete-states removes that game's states). MBC3 clock data is
not kept by Goomba (it uses the GBA's clock): a clock block at the end of a plain save is dropped on
import and none is written on export.
"""
import argparse, os, shutil, struct, sys

STATEID, STATEID2 = 0x57A731D8, 0x57A731D9
STATESAVE, SRAMSAVE, CONFIGSAVE = 0, 1, 2
HEADER = 48                     # stateheader: size, type, uncompressed size, frame, checksum, title[32]
SAVE_START = {0x8000: 0x6000, 0x10000: 0xE000}   # GBA SRAM size -> end of the saves (live 8KB after it)
LIVE32_START = 0x8000           # a 32KB game's live cart RAM (64KB GBA SRAM, config live_sram)
TYPES = {STATESAVE: "state", SRAMSAVE: "cart RAM", CONFIGSAVE: "config"}


# ---------------------------------------------------------------- LZO1X (minilzo's format)

def lzo_decompress(src):
    out = bytearray()
    ip, state = 0, 0

    def length(base, mask):
        nonlocal ip
        t = src[ip] & mask
        ip += 1
        if t == 0:
            t = base
            while src[ip] == 0:
                t += 255
                ip += 1
            t += src[ip]
            ip += 1
        return t

    if src[0] > 17:
        t = src[0] - 17
        ip = 1
        out += src[ip:ip + t]
        ip += t
        state = 4 if t >= 4 else t
    while True:
        t = src[ip]
        if t >= 64:
            ip += 1
            dist = ((t >> 2) & 7) + (src[ip] << 3) + 1
            ip += 1
            ln, st = (t >> 5) + 1, t & 3
        elif t >= 32:
            ln = length(31, 31) + 2
            d = src[ip] | src[ip + 1] << 8
            ip += 2
            dist, st = (d >> 2) + 1, d & 3
        elif t >= 16:
            hi = (t & 8) << 11
            ln = length(7, 7) + 2
            d = src[ip] | src[ip + 1] << 8
            ip += 2
            dist, st = hi + (d >> 2), d & 3
            if dist == 0:
                return bytes(out)
            dist += 0x4000
        else:
            ip += 1
            if state == 0:              # literal run
                if t == 0:
                    t = 15
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += src[ip]
                    ip += 1
                t += 3
                out += src[ip:ip + t]
                ip += t
                state = 4
                continue
            if state < 4:               # 2-byte match after a short literal run
                dist, ln = (t >> 2) + (src[ip] << 2) + 1, 2
            else:                       # 3-byte match after a long literal run
                dist, ln = (t >> 2) + (src[ip] << 2) + 2049, 3
            ip += 1
            st = t & 3
        if dist > len(out):
            raise ValueError("corrupt LZO data")
        for _ in range(ln):
            out.append(out[-dist])
        out += src[ip:ip + st]
        ip += st
        state = st


def lzo_compress(data):
    """Greedy LZO1X compressor; any minilzo lzo1x_decompress reads its output."""
    n = len(data)
    out = bytearray()
    table = {}
    lit = 0                             # start of the pending literal run
    ip = 4                              # the first run has at least 4 literals (or is the whole input)

    def put_literals(end):
        nonlocal out
        t = end - lit
        if t == 0:
            return
        if not out and t <= 238:
            out.append(17 + t)
        elif t <= 3:
            out[-2] |= t                # stuffed into the previous match
        elif t <= 18:
            out.append(t - 3)
        else:
            tt = t - 18
            out.append(0)
            while tt > 255:
                tt -= 255
                out.append(0)
            out.append(tt)
        out += data[lit:end]

    while ip + 3 <= n:
        key = data[ip:ip + 3]
        cand = table.get(key)
        table[key] = ip
        if cand is None or ip - cand > 0xBFFF:
            ip += 1
            continue
        m_len = 3
        while ip + m_len < n and data[cand + m_len] == data[ip + m_len]:
            m_len += 1
        m_off = ip - cand
        put_literals(ip)
        if m_len <= 8 and m_off <= 0x800:
            m_off -= 1
            out += bytes(((m_len - 1) << 5 | (m_off & 7) << 2, m_off >> 3))
        elif m_off <= 0x4000:
            m_off -= 1
            if m_len <= 33:
                out.append(32 | (m_len - 2))
            else:
                rest = m_len - 33
                out.append(32)
                while rest > 255:
                    rest -= 255
                    out.append(0)
                out.append(rest)
            out += bytes(((m_off << 2) & 0xFF, (m_off >> 6) & 0xFF))
        else:
            m_off -= 0x4000
            hi = (m_off >> 11) & 8
            if m_len <= 9:
                out.append(16 | hi | (m_len - 2))
            else:
                rest = m_len - 9
                out.append(16 | hi)
                while rest > 255:
                    rest -= 255
                    out.append(0)
                out.append(rest)
            out += bytes(((m_off << 2) & 0xFF, (m_off >> 6) & 0xFF))
        for k in range(ip + 1, min(ip + m_len, n - 2)):
            table[data[k:k + 3]] = k
        ip += m_len
        lit = ip
    put_literals(n)
    out += b"\x11\x00\x00"              # end of stream
    return bytes(out)


# ---------------------------------------------------------------- Goomba's GBA save

def rom_checksum(rom):
    """Goomba's checksum_this/checksum_mem: 128 words, every 128 bytes of the first 16KB."""
    rom = rom[:0x4000]
    if len(rom) < 0x4000:
        rom = rom + bytes([rom[-1]]) * (0x4000 - len(rom))
    return sum(struct.unpack_from("<I", rom, i * 128)[0] for i in range(128)) & 0xFFFFFFFF


def rom_title(rom):
    return rom[0x134:0x143].split(b"\0")[0]


def cart_ram_size(rom):
    """Bytes Goomba keeps for this cart's RAM (save_new_sram), or 0."""
    if rom[0x147] in (5, 6):
        return 512                      # MBC2: 512 x 4 bits
    return {1: 0x2000, 2: 0x2000, 3: 0x8000, 4: 0x8000}.get(rom[0x149], 0)


class Entry:
    def __init__(self, raw):
        self.raw = bytearray(raw)

    size = property(lambda s: struct.unpack_from("<H", s.raw, 0)[0])
    type = property(lambda s: struct.unpack_from("<H", s.raw, 2)[0])
    uncompressed = property(lambda s: struct.unpack_from("<I", s.raw, 4)[0])
    checksum = property(lambda s: struct.unpack_from("<I", s.raw, 12)[0])
    title = property(lambda s: s.raw[16:48].split(b"\0")[0].decode("latin-1"))

    # config entries
    live_sram = property(lambda s: s.raw[7])
    owner = property(lambda s: struct.unpack_from("<I", s.raw, 8)[0])

    def set_owner(self, owner, live):
        struct.pack_into("<I", self.raw, 8, owner)
        self.raw[7] = live

    def cart_ram(self):
        return lzo_decompress(bytes(self.raw[HEADER:self.size]))[:self.uncompressed]


def make_sram_entry(ram, checksum, title, old=None):
    """A cart RAM entry; an old entry's header (title, frame count) is kept, like Goomba does."""
    packed = lzo_compress(ram)
    size = (HEADER + len(packed) + 3) & ~3
    head = bytearray(old.raw[:HEADER]) if old else bytearray(HEADER)
    struct.pack_into("<HHIII", head, 0, size, SRAMSAVE, len(ram),
                     struct.unpack_from("<I", head, 8)[0] if old else 0, checksum)
    if not old:
        head[16:48] = title[:15].ljust(32, b"\0")
    return Entry(bytes(head) + packed + bytes(size - HEADER - len(packed)))


class GoombaSave:
    def __init__(self, data):
        if len(data) not in SAVE_START:
            raise SystemExit("a Goomba GBA save is 32KB or 64KB, this one is %d bytes" % len(data))
        self.data = bytearray(data)
        self.save_start = SAVE_START[len(data)]
        self.entries = []
        if struct.unpack_from("<I", data, 0)[0] not in (STATEID, STATEID2):
            return                      # blank: Goomba initialises it on first use
        off = 4
        while off + HEADER <= self.save_start:
            size = struct.unpack_from("<H", data, off)[0]
            if size == 0:
                break
            if size < HEADER or off + size > self.save_start:
                raise SystemExit("damaged save: bad entry at offset 0x%x" % off)
            self.entries.append(Entry(data[off:off + size]))
            off += size

    def config(self):
        return next((e for e in self.entries if e.type == CONFIGSAVE), None)

    def find(self, type_, checksum):
        return [e for e in self.entries if e.type == type_ and e.checksum == checksum]

    def live(self):
        """(owner checksum, its live cart RAM area) or None."""
        cfg = self.config()
        if not cfg or not cfg.owner:
            return None
        if cfg.live_sram and len(self.data) == 0x10000:
            return cfg.owner, bytes(self.data[LIVE32_START:])
        return cfg.owner, bytes(self.data[self.save_start:self.save_start + 0x2000])

    def cart_ram(self, checksum):
        """The game's cart RAM: its live copy if it owns it (newer), else its packed entry."""
        ents = self.find(SRAMSAVE, checksum)
        if not ents:
            return None
        live = self.live()
        if live and live[0] == checksum:
            return live[1][:ents[-1].uncompressed]
        return ents[-1].cart_ram()

    def settle(self):
        """Pack the live owner's cart RAM into its entry and release it, as Goomba does at boot."""
        live = self.live()
        if not live:
            return
        ents = self.find(SRAMSAVE, live[0])
        if ents:
            old = ents[-1]
            new = make_sram_entry(live[1][:old.uncompressed], old.checksum, None, old)
            self.entries[self.entries.index(old)] = new
        self.config().set_owner(0, 0)
        self.data[self.save_start:] = bytes(len(self.data) - self.save_start)

    def write(self):
        body = b"".join(bytes(e.raw) for e in self.entries)
        if 4 + len(body) + 8 > self.save_start:
            raise SystemExit("does not fit: %d bytes of saves, %d available (delete some states in "
                             "Goomba's menu or use --delete-states)" % (4 + len(body) + 8, self.save_start))
        start = struct.pack("<I", STATEID) + body + struct.pack("<II", 0, 0xFFFFFFFF)
        self.data[:self.save_start] = start + bytes(self.save_start - len(start))
        return bytes(self.data)


# ---------------------------------------------------------------- commands

def cmd_list(a):
    sav = GoombaSave(open(a.goomba, "rb").read())
    roms = {}
    for path in a.roms:
        rom = open(path, "rb").read()
        roms[rom_checksum(rom)] = os.path.basename(path)
    live = sav.live()
    print("%dKB GBA save, %d of %d bytes used" % (len(sav.data) // 1024,
          4 + sum(e.size for e in sav.entries) + 8, sav.save_start))
    for e in sav.entries:
        if e.type == CONFIGSAVE:
            continue
        extra = ""
        if e.type == SRAMSAVE and live and live[0] == e.checksum:
            extra = "  (live copy is newer)"
        print("  %-8s %-16s %5d bytes  checksum %08x%s%s" % (TYPES.get(e.type, "?"), e.title, e.size,
              e.checksum, ("  " + roms[e.checksum]) if e.checksum in roms else "", extra))


def cmd_export(a):
    sav = GoombaSave(open(a.goomba, "rb").read())
    rom = open(a.rom, "rb").read()
    ram = sav.cart_ram(rom_checksum(rom))
    if ram is None:
        raise SystemExit("no cart RAM for %s (checksum %08x) in %s"
                         % (rom_title(rom).decode("latin-1"), rom_checksum(rom), a.goomba))
    want = cart_ram_size(rom)
    if rom[0x149] == 1:
        ram = ram[:0x800]               # 2KB carts: Goomba keeps 8KB
    elif want and want < len(ram):
        ram = ram[:want]
    open(a.out, "wb").write(ram)
    print("wrote %s: %d bytes of cart RAM for %s" % (a.out, len(ram), rom_title(rom).decode("latin-1")))


def cmd_import(a):
    data = open(a.goomba, "rb").read()
    sav = GoombaSave(data)
    rom = open(a.rom, "rb").read()
    chk = rom_checksum(rom)
    size = cart_ram_size(rom)
    if not size:
        raise SystemExit("%s has no battery-backed cart RAM Goomba saves" % a.rom)
    if rom[0x149] in (4, 5):
        print("warning: this cart has more RAM than Goomba keeps (32KB)", file=sys.stderr)
    ram = open(a.save, "rb").read()
    expect = 0x800 if rom[0x149] == 1 else size
    if len(ram) in (expect + 44, expect + 48):
        print("dropping the %d-byte clock block (Goomba uses the GBA's clock)" % (len(ram) - expect))
        ram = ram[:expect]
    elif len(ram) != expect and not (size == 0x2000 and len(ram) == 0x800):
        raise SystemExit("%s is %d bytes; this cart's RAM is %d" % (a.save, len(ram), expect))
    ram = ram + bytes(size - len(ram))  # 2KB carts: Goomba keeps 8KB

    sav.settle()
    old = sav.find(SRAMSAVE, chk)
    new = make_sram_entry(ram, chk, rom_title(rom), old[-1] if old else None)
    if old:
        sav.entries[sav.entries.index(old[-1])] = new
    else:
        sav.entries.append(new)
    states = sav.find(STATESAVE, chk)
    if states and a.delete_states:
        sav.entries = [e for e in sav.entries if e not in states]
        print("deleted %d saved state(s) of this game" % len(states))
    elif states:
        print("note: %d saved state(s) of this game hold older cart RAM; loading one brings it back "
              "(--delete-states removes them)" % len(states))
    out = sav.write()
    target = a.out or a.goomba
    if not a.out:
        shutil.copyfile(a.goomba, a.goomba + ".bak")
    open(target, "wb").write(out)
    print("wrote %s: %s's cart RAM (%d bytes) from %s" % (target, rom_title(rom).decode("latin-1"), size, a.save))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="what the GBA save holds")
    p.add_argument("goomba")
    p.add_argument("roms", nargs="*", help="ROMs to name the entries with")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("export", help="a game's cart RAM as a plain save")
    p.add_argument("goomba")
    p.add_argument("rom")
    p.add_argument("out")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("import", help="a plain save into the GBA save")
    p.add_argument("goomba")
    p.add_argument("rom")
    p.add_argument("save")
    p.add_argument("--delete-states", action="store_true", help="remove this game's saved states")
    p.add_argument("--out", help="write here instead of rewriting GOOMBA.sav")
    p.set_defaults(func=cmd_import)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

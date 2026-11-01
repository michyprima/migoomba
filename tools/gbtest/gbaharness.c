/* Headless GBA harness on libmgba. Line commands on stdin, each answered with output then "ok".
 *   frames N            run N video frames
 *   cycles              print exact GBA cycles since reset
 *   frame               print frame counter
 *   keys HEX            set GBA key mask (A=1 B=2 Sel=4 Start=8 R=10 L=20 U=40 D=80 RB=100 LB=200)
 *   r ADDR LEN          hex dump (bus view)
 *   w ADDR HEX          write bytes (bus view)
 *   shot PATH           write current 240x160 frame as PNG
 *   profile N PATH      step N frames instruction by instruction; write "pc cycles count" per PC
 *   until ADDR MAXFRAMES [REG DELTA HEXBYTE]  step until the executing PC == ADDR (and, if given,
 *                       byte at gprs[REG]+DELTA == HEXBYTE): "hit", or "timeout" after MAXFRAMES
 *   step N              execute N ARM instructions
 *   stepframes N        like frames, but stepped so idle time is accounted (see stats)
 *   stats               "total idle" GBA cycles over stepped execution since last stats; resets
 *   regs                r0-r15 and cpsr
 *   quit
 */
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
#include <mgba/core/timing.h>
#include <stdarg.h>
#include <mgba/internal/arm/arm.h>
#include <mgba/internal/arm/isa-inlines.h>
#include <mgba/internal/gba/gba.h>
#include <mgba-util/vfs.h>
#include <zlib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void quietLog(struct mLogger* logger, int category, enum mLogLevel level, const char* format, va_list args) {
	(void) logger;
	static const char* seen[64];
	static int seenCount[64];
	if (!(level & (mLOG_WARN | mLOG_ERROR | mLOG_FATAL))) {
		return;
	}
	/* print each distinct message format at most 3 times */
	int i;
	for (i = 0; i < 64 && seen[i] && seen[i] != format; ++i);
	if (i < 64) {
		seen[i] = format;
		if (++seenCount[i] > 3) {
			return;
		}
	}
	fprintf(stderr, "[%s] ", mLogCategoryName(category));
	vfprintf(stderr, format, args);
	fputc('\n', stderr);
}
static struct mLogger logger = { .log = quietLog };

#define W 240
#define H 160
static color_t video[W * H];

static void put32(FILE* f, uint32_t v) {
	uint8_t b[4] = { v >> 24, v >> 16, v >> 8, v };
	fwrite(b, 1, 4, f);
}

static void chunk(FILE* f, const char* type, const uint8_t* data, uint32_t len) {
	put32(f, len);
	fwrite(type, 1, 4, f);
	fwrite(data, 1, len, f);
	uint32_t crc = crc32(0, (const uint8_t*) type, 4);
	crc = crc32(crc, data, len);
	put32(f, crc);
}

static int writePNG(const char* path) {
	size_t rawLen = H * (1 + W * 3);
	uint8_t* raw = malloc(rawLen);
	uint8_t* p = raw;
	for (int y = 0; y < H; ++y) {
		*p++ = 0;
		for (int x = 0; x < W; ++x) {
			uint32_t c = video[y * W + x]; /* XBGR8 */
			*p++ = c;
			*p++ = c >> 8;
			*p++ = c >> 16;
		}
	}
	uLongf zLen = compressBound(rawLen);
	uint8_t* z = malloc(zLen);
	compress(z, &zLen, raw, rawLen);
	FILE* f = fopen(path, "wb");
	if (!f) {
		free(raw);
		free(z);
		return 0;
	}
	fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
	uint8_t ihdr[13] = { 0, 0, 0, W, 0, 0, 0, H, 8, 2, 0, 0, 0 };
	chunk(f, "IHDR", ihdr, 13);
	chunk(f, "IDAT", z, zLen);
	chunk(f, "IEND", NULL, 0);
	fclose(f);
	free(raw);
	free(z);
	return 1;
}

/* open-addressing PC -> (cycles, count) */
#define HBITS 20
static uint32_t hkey[1 << HBITS];
static uint64_t hcyc[1 << HBITS];
static uint64_t hcnt[1 << HBITS];
static uint8_t hused[1 << HBITS];

static void hadd(uint32_t pc, uint64_t cyc) {
	uint32_t i = (pc * 2654435761u) >> (32 - HBITS);
	while (hused[i] && hkey[i] != pc) {
		i = (i + 1) & ((1 << HBITS) - 1);
	}
	hused[i] = 1;
	hkey[i] = pc;
	hcyc[i] += cyc;
	hcnt[i] += 1;
}

/* Any single step longer than this is the CPU halted (IntrWait/VBlankIntrWait/HALTCNT), i.e. idle. */
#define IDLE_STEP_CYCLES 1000
static uint64_t statTotal, statIdle;

static uint64_t stepOne(struct mCore* core, struct GBA* gba) {
	uint64_t t0 = mTimingGlobalTime(&gba->timing);
	core->step(core);
	uint64_t d = mTimingGlobalTime(&gba->timing) - t0;
	statTotal += d;
	if (d > IDLE_STEP_CYCLES) {
		statIdle += d;
	}
	return d;
}

static void profile(struct mCore* core, struct GBA* gba, int frames, const char* path) {
	memset(hused, 0, sizeof(hused));
	memset(hcyc, 0, sizeof(hcyc));
	memset(hcnt, 0, sizeof(hcnt));
	uint32_t end = core->frameCounter(core) + frames;
	uint64_t start = mTimingGlobalTime(&gba->timing);
	while (core->frameCounter(core) < end) {
		uint32_t pc = _ARMPCAddress(gba->cpu);
		hadd(pc, stepOne(core, gba));
	}
	FILE* f = fopen(path, "w");
	for (uint32_t i = 0; i < (1u << HBITS); ++i) {
		if (hused[i]) {
			fprintf(f, "%08x %llu %llu\n", hkey[i], (unsigned long long) hcyc[i], (unsigned long long) hcnt[i]);
		}
	}
	fclose(f);
	printf("%llu\n", (unsigned long long) (mTimingGlobalTime(&gba->timing) - start));
}

int main(int argc, char** argv) {
	if (argc < 2) {
		fprintf(stderr, "usage: %s ROM.gba\n", argv[0]);
		return 1;
	}
	mLogSetDefaultLogger(&logger);
	struct mCore* core = mCoreFind(argv[1]);
	if (!core || !core->init(core)) {
		fprintf(stderr, "cannot create core\n");
		return 1;
	}
	mCoreInitConfig(core, NULL);
	core->setVideoBuffer(core, video, W);
	if (!mCoreLoadFile(core, argv[1])) {
		fprintf(stderr, "cannot load %s\n", argv[1]);
		return 1;
	}
	core->reset(core);
	struct GBA* gba = core->board;

	char line[1024];
	setvbuf(stdout, NULL, _IOLBF, 0);
	while (fgets(line, sizeof(line), stdin)) {
		char cmd[32] = { 0 }, arg2[900] = { 0 };
		unsigned long a = 0, b = 0;
		int n = sscanf(line, "%31s %lx %899s", cmd, &a, arg2);
		if (!strcmp(cmd, "frames")) {
			a = strtoul(line + 7, NULL, 10);
			for (unsigned long i = 0; i < a; ++i) {
				core->runFrame(core);
			}
		} else if (!strcmp(cmd, "cycles")) {
			printf("%llu\n", (unsigned long long) mTimingGlobalTime(&gba->timing));
		} else if (!strcmp(cmd, "frame")) {
			printf("%u\n", core->frameCounter(core));
		} else if (!strcmp(cmd, "keys") && n >= 2) {
			core->setKeys(core, a);
		} else if (!strcmp(cmd, "r") && n >= 3) {
			b = strtoul(arg2, NULL, 0);
			for (unsigned long i = 0; i < b; ++i) {
				printf("%02x", core->busRead8(core, a + i));
			}
			printf("\n");
		} else if (!strcmp(cmd, "w") && n >= 3) {
			for (size_t i = 0; arg2[2 * i] && arg2[2 * i + 1]; ++i) {
				unsigned v;
				sscanf(arg2 + 2 * i, "%2x", &v);
				core->busWrite8(core, a + i, v);
			}
		} else if (!strcmp(cmd, "shot")) {
			char path[900];
			if (sscanf(line, "%*s %899s", path) == 1 && !writePNG(path)) {
				printf("error\n");
			}
		} else if (!strcmp(cmd, "profile")) {
			int frames;
			char path[900];
			if (sscanf(line, "%*s %d %899s", &frames, path) == 2) {
				profile(core, gba, frames, path);
			}
		} else if (!strcmp(cmd, "until") && n >= 3) {
			/* optional condition: byte at (gprs[reg] + delta) == val */
			int reg = -1, delta = 0;
			unsigned val = 0;
			sscanf(line, "%*s %*s %*s %d %d %x", &reg, &delta, &val);
			uint32_t end = core->frameCounter(core) + strtoul(arg2, NULL, 0);
			int hit = 0;
			while (core->frameCounter(core) < end) {
				if (_ARMPCAddress(gba->cpu) == a &&
				    (reg < 0 || core->busRead8(core, gba->cpu->gprs[reg & 15] + delta) == val)) {
					hit = 1;
					break;
				}
				stepOne(core, gba);
			}
			printf("%s\n", hit ? "hit" : "timeout");
		} else if (!strcmp(cmd, "step")) {
			unsigned long count = strtoul(line + 5, NULL, 10);
			for (unsigned long i = 0; i < count; ++i) {
				stepOne(core, gba);
			}
		} else if (!strcmp(cmd, "stepframes")) {
			uint32_t end = core->frameCounter(core) + strtoul(line + 11, NULL, 10);
			while (core->frameCounter(core) < end) {
				stepOne(core, gba);
			}
		} else if (!strcmp(cmd, "stats")) {
			printf("%llu %llu\n", (unsigned long long) statTotal, (unsigned long long) statIdle);
			statTotal = statIdle = 0;
		} else if (!strcmp(cmd, "regs")) {
			for (int i = 0; i < 16; ++i) {
				printf("%08x ", gba->cpu->gprs[i]);
			}
			printf("%08x\n", gba->cpu->cpsr.packed);
		} else if (!strcmp(cmd, "quit")) {
			break;
		} else if (cmd[0]) {
			printf("error unknown command\n");
		}
		printf("ok\n");
	}
	core->deinit(core);
	return 0;
}

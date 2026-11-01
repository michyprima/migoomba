/* Headless GBA harness on libmgba. Line commands on stdin, each answered with output then "ok".
 * usage: gbaharness ROM.gba [SAVE.sav]   (SAVE is the GBA's battery save, kept across runs)
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
 *   stepall 0|1         make "frames" step instructions too (so stack tracking covers everything)
 *   stackstats          lowest SP in user/system mode, lowest SP in IRQ mode, and PC/LR at the lowest
 *                       user SP, while stepping; resets
 *   trace ADDR MAXFRAMES  step until the executing PC == ADDR; print the last 64 instructions
 *                       before it (pc, cpsr, r0-r3, r9, sp, lr), then "hit" or "timeout"
 *   watch ADDR LEN MAXFRAMES  step until one of LEN (<=256) bytes at ADDR changes; print the PC that
 *                       changed it and r0-r15 before it ran, then "hit" or "timeout"
 *   dma                 per DMA channel: control, next source and next destination
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
#include "pngwrite.h"

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

/* Idle = steps taken while the CPU is halted (IntrWait/VBlankIntrWait/HALTCNT). Steps longer than this
 * also count, as before. Halted steps are short when interrupts come every scanline (HBlank IRQs), so
 * the halted flag is what matters. */
#define IDLE_STEP_CYCLES 1000
/* profile: PC under which halted time is recorded */
#define IDLE_PC 0xFFFFFFFEu
static int lastStepHalted;
static uint64_t statTotal, statIdle;
/* lowest SP seen in user/system mode and in IRQ mode while stepping */
static uint32_t minSpUsr = 0xFFFFFFFF, minSpIrq = 0xFFFFFFFF, minSpUsrPc, minSpUsrLr;
static int stepAll;

static uint64_t stepOne(struct mCore* core, struct GBA* gba) {
	uint64_t t0 = mTimingGlobalTime(&gba->timing);
	lastStepHalted = gba->cpu->halted;
	core->step(core);
	uint32_t sp = gba->cpu->gprs[ARM_SP];
	if (gba->cpu->cpsr.priv == MODE_IRQ) {
		if (sp < minSpIrq) {
			minSpIrq = sp;
		}
	} else if (gba->cpu->cpsr.priv == MODE_SYSTEM || gba->cpu->cpsr.priv == MODE_USER) {
		if (sp < minSpUsr) {
			minSpUsr = sp;
			minSpUsrPc = _ARMPCAddress(gba->cpu);
			minSpUsrLr = gba->cpu->gprs[ARM_LR];
		}
	}
	uint64_t d = mTimingGlobalTime(&gba->timing) - t0;
	statTotal += d;
	if (lastStepHalted || d > IDLE_STEP_CYCLES) {
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
		uint64_t d = stepOne(core, gba);
		hadd(lastStepHalted ? IDLE_PC : pc, d);
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
	if (argc > 2 && !mCoreLoadSaveFile(core, argv[2], false)) {
		fprintf(stderr, "cannot open save %s\n", argv[2]);
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
			if (stepAll) {
				uint32_t end = core->frameCounter(core) + a;
				while (core->frameCounter(core) < end) {
					stepOne(core, gba);
				}
			} else {
				for (unsigned long i = 0; i < a; ++i) {
					core->runFrame(core);
				}
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
			if (sscanf(line, "%*s %899s", path) == 1 && !writePNG(path, (const uint32_t*) video, W, H, W)) {
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
		} else if (!strcmp(cmd, "stepall") && n >= 2) {
			stepAll = a != 0;
		} else if (!strcmp(cmd, "stackstats")) {
			printf("%08x %08x %08x %08x\n", minSpUsr, minSpIrq, minSpUsrPc, minSpUsrLr);
			minSpUsr = minSpIrq = 0xFFFFFFFF;
		} else if (!strcmp(cmd, "trace") && n >= 3) {
			/* step until PC == ADDR, then print the last TRACE_LEN instructions before it */
			enum { TRACE_LEN = 64 };
			static uint32_t tr[TRACE_LEN][9];
			unsigned long pos = 0;
			uint32_t end = core->frameCounter(core) + strtoul(arg2, NULL, 10);
			int hit = 0;
			while (core->frameCounter(core) < end) {
				uint32_t pc = _ARMPCAddress(gba->cpu);
				if (pc == a) {
					hit = 1;
					break;
				}
				uint32_t* e = tr[pos++ % TRACE_LEN];
				e[0] = pc;
				e[1] = gba->cpu->cpsr.packed;
				for (int i = 0; i < 4; ++i) {
					e[2 + i] = gba->cpu->gprs[i];
				}
				e[6] = gba->cpu->gprs[9];
				e[7] = gba->cpu->gprs[ARM_SP];
				e[8] = gba->cpu->gprs[ARM_LR];
				stepOne(core, gba);
			}
			for (unsigned long i = pos > TRACE_LEN ? pos - TRACE_LEN : 0; i < pos; ++i) {
				uint32_t* e = tr[i % TRACE_LEN];
				printf("pc=%08x cpsr=%08x r0=%08x r1=%08x r2=%08x r3=%08x r9=%08x sp=%08x lr=%08x\n",
				       e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8]);
			}
			printf(hit ? "hit\n" : "timeout\n");
		} else if (!strcmp(cmd, "watch") && n >= 3) {
			/* watch ADDR LEN MAXFRAMES: step until any of LEN bytes at ADDR changes, then print the
			 * PC of the instruction that changed it and the registers before it ran */
			unsigned long len = 0, maxf = 0;
			sscanf(line, "%*s %*lx %lu %lu", &len, &maxf);
			if (len > 256) {
				len = 256;
			}
			uint8_t before[256];
			for (unsigned long i = 0; i < len; ++i) {
				before[i] = core->rawRead8(core, a + i, -1);
			}
			uint32_t end = core->frameCounter(core) + maxf;
			int hit = 0;
			while (!hit && core->frameCounter(core) < end) {
				uint32_t pc = _ARMPCAddress(gba->cpu);
				uint32_t r[16];
				memcpy(r, gba->cpu->gprs, sizeof(r));
				stepOne(core, gba);
				for (unsigned long i = 0; i < len; ++i) {
					uint8_t v = core->rawRead8(core, a + i, -1);
					if (v != before[i]) {
						printf("pc=%08x offset=%lu %02x->%02x", pc, i, before[i], v);
						for (int k = 0; k < 16; ++k) {
							printf(" r%d=%08x", k, r[k]);
						}
						printf("\n");
						hit = 1;
						break;
					}
				}
			}
			printf(hit ? "hit\n" : "timeout\n");
		} else if (!strcmp(cmd, "dma")) {
			/* per channel: control, source and destination of the next transfer */
			for (int i = 0; i < 4; ++i) {
				struct GBADMA* d = &gba->memory.dma[i];
				printf("%04x %08x %08x%c", d->reg, d->nextSource, d->nextDest, i == 3 ? '\n' : ' ');
			}
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

/* Headless Game Boy reference renderer on mGBA's own GB core. Line commands on stdin, each answered
 * with output then "ok" (same protocol as gbaharness).
 * usage: gbref ROM.gb MODEL [SAVE]   MODEL: DMG, SGB, CGB or AGB (screenshots are always the 160x144 GB screen);
 *                                    SAVE: battery save to load (a copy; the file is not written)
 *   frames N            run N GB frames
 *   frame               print the GB frame counter
 *   keys HEX            set GB keys (A=1 B=2 Sel=4 Start=8 R=10 L=20 U=40 D=80)
 *   shot PATH           write the 160x144 screen as PNG
 *   vram PATH           write both 8 KB VRAM banks to PATH
 *   peek ADDR N         print N bytes of GB memory from ADDR (current banks) in hex
 *   sram PATH           write the cartridge RAM to PATH
 *   cgbpal              print the 64 CGB palette words (BG 0-31, OBJ 32-63) in hex
 *   iolog N PATH [LO HI]  run N GB frames one instruction at a time and append each write to a video register
 *                       (FF40-FF4F, FF51-FF55, FF68-FF6B), or to LO-HI (hex), to PATH as "frame ly pc reg value"
 *   quit
 */
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
#include <mgba/internal/gb/gb.h>
#include <mgba/internal/gb/io.h>
#include <mgba/internal/sm83/sm83.h>
#include <mgba-util/vfs.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pngwrite.h"

static void quietLog(struct mLogger* logger, int category, enum mLogLevel level, const char* format, va_list args) {
	(void) logger;
	(void) category;
	(void) level;
	(void) format;
	(void) args;
}
static struct mLogger logger = { .log = quietLog };

#define W 160
#define H 144
static color_t video[256 * 224];

/* address a video register write by the instruction at PC goes to, or -1 */
static int ioTarget(struct mCore* core, struct SM83Core* cpu, uint8_t* value, unsigned lo, unsigned hi) {
	uint8_t op = core->rawRead8(core, cpu->pc, -1);
	int addr = -1;
	*value = cpu->a;
	if (op == 0xE0) {
		addr = 0xFF00 | core->rawRead8(core, cpu->pc + 1, -1);
	} else if (op == 0xE2) {
		addr = 0xFF00 | cpu->c;
	} else if (op == 0xEA) {
		addr = core->rawRead8(core, cpu->pc + 1, -1) | core->rawRead8(core, cpu->pc + 2, -1) << 8;
	} else if (op == 0x02) {
		addr = cpu->bc;
	} else if (op == 0x12) {
		addr = cpu->de;
	} else if (op == 0x22 || op == 0x32 || op == 0x77) {
		addr = cpu->hl;
	} else if (op == 0x36) {
		addr = cpu->hl;
		*value = core->rawRead8(core, cpu->pc + 1, -1);
	} else if ((op & 0xCF) == 0xC5) {
		const uint8_t r[] = { cpu->b, cpu->d, cpu->h, cpu->a };
		addr = (cpu->sp - 1) & 0xFFFF;	/* the high byte; the low byte goes below it */
		*value = r[(op >> 4) & 3];
	} else if (op >= 0x70 && op <= 0x75) {
		const uint8_t r[] = { cpu->b, cpu->c, cpu->d, cpu->e, cpu->h, cpu->l };
		addr = cpu->hl;
		*value = r[op - 0x70];
	}
	if (hi) {
		return addr >= (int) lo && addr <= (int) hi ? addr : -1;
	}
	if ((addr >= 0xFF40 && addr <= 0xFF4F) || (addr >= 0xFF51 && addr <= 0xFF55) || (addr >= 0xFF68 && addr <= 0xFF6B)) {
		return addr;
	}
	return -1;
}

int main(int argc, char** argv) {
	if (argc < 3) {
		fprintf(stderr, "usage: %s ROM.gb DMG|SGB|CGB|AGB [SAVE]\n", argv[0]);
		return 1;
	}
	mLogSetDefaultLogger(&logger);
	struct mCore* core = mCoreFind(argv[1]);
	if (!core || !core->init(core)) {
		fprintf(stderr, "cannot create core\n");
		return 1;
	}
	mCoreInitConfig(core, NULL);
	/* every header combination maps to the requested model */
	const char* keys[] = { "gb.model", "sgb.model", "cgb.model", "cgb.hybridModel", "cgb.sgbModel" };
	for (size_t i = 0; i < sizeof(keys) / sizeof(*keys); ++i) {
		mCoreConfigSetValue(&core->config, keys[i], argv[2]);
	}
	mCoreConfigSetIntValue(&core->config, "sgb.borders", 0);
	mCoreConfigSetIntValue(&core->config, "skipBios", 1);
	core->setVideoBuffer(core, video, 256);
	if (!mCoreLoadFile(core, argv[1])) {
		fprintf(stderr, "cannot load %s\n", argv[1]);
		return 1;
	}
	mCoreLoadConfig(core);
	if (argc > 3) {
		/* load the save into memory so the file itself is never written */
		struct VFile* f = VFileOpen(argv[3], O_RDONLY);
		struct VFile* mem = f ? VFileMemChunk(NULL, f->size(f)) : NULL;
		if (!mem) {
			fprintf(stderr, "cannot read %s\n", argv[3]);
			return 1;
		}
		char buf[4096];
		ssize_t n;
		while ((n = f->read(f, buf, sizeof(buf))) > 0) {
			mem->write(mem, buf, n);
		}
		f->close(f);
		mem->seek(mem, 0, SEEK_SET);
		core->loadSave(core, mem);
	}
	core->reset(core);

	char line[1024];
	setvbuf(stdout, NULL, _IOLBF, 0);
	while (fgets(line, sizeof(line), stdin)) {
		char cmd[32] = { 0 }, arg[900] = { 0 };
		int n = sscanf(line, "%31s %899s", cmd, arg);
		if (!strcmp(cmd, "frames") && n >= 2) {
			uint32_t end = core->frameCounter(core) + strtoul(arg, NULL, 10);
			while (core->frameCounter(core) < end) {
				core->runFrame(core);
			}
		} else if (!strcmp(cmd, "frame")) {
			printf("%u\n", core->frameCounter(core));
		} else if (!strcmp(cmd, "keys") && n >= 2) {
			core->setKeys(core, strtoul(arg, NULL, 16));
		} else if (!strcmp(cmd, "shot") && n >= 2) {
			unsigned w, h;
			core->desiredVideoDimensions(core, &w, &h);
			/* SGB games may still render the 256x224 border frame: the GB screen sits at (48,40) */
			const color_t* px = w == 256 && h == 224 ? &video[40 * 256 + 48] : video;
			if (!(w == W && h == H) && !(w == 256 && h == 224)) {
				printf("error shot %ux%u\n", w, h);
			} else if (!writePNG(arg, (const uint32_t*) px, W, H, 256)) {
				printf("error shot %ux%u\n", w, h);
			}
		} else if (!strcmp(cmd, "vram") && n >= 2) {
			struct GB* gb = core->board;
			FILE* f = fopen(arg, "wb");
			if (!f || fwrite(gb->video.vram, 1, 0x4000, f) != 0x4000) {
				printf("error cannot write %s\n", arg);
			}
			if (f) {
				fclose(f);
			}
		} else if (!strcmp(cmd, "peek") && n >= 2) {
			unsigned long len = 1;
			sscanf(line, "%*s %*s %lu", &len);
			for (unsigned long i = 0; i < len; ++i) {
				printf("%02x", core->rawRead8(core, (strtoul(arg, NULL, 16) + i) & 0xFFFF, -1));
			}
			printf("\n");
		} else if (!strcmp(cmd, "sram") && n >= 2) {
			struct GB* gb = core->board;
			FILE* f = fopen(arg, "wb");
			if (!f || fwrite(gb->memory.sram, 1, gb->sramSize, f) != gb->sramSize) {
				printf("error cannot write %s\n", arg);
			}
			if (f) {
				fclose(f);
			}
		} else if (!strcmp(cmd, "cgbpal")) {
			struct GB* gb = core->board;
			for (int i = 0; i < 64; ++i) {
				printf("%04x%c", gb->video.palette[i], i == 63 ? '\n' : ' ');
			}
		} else if (!strcmp(cmd, "iolog") && n >= 2) {
			char path[900] = { 0 };
			unsigned lo = 0, hi = 0;
			sscanf(line, "%*s %*s %899s %x %x", path, &lo, &hi);
			FILE* f = fopen(path, "a");
			struct GB* gb = core->board;
			struct SM83Core* cpu = core->cpu;
			uint32_t end = core->frameCounter(core) + strtoul(arg, NULL, 10);
			while (f && core->frameCounter(core) < end) {
				uint8_t v;
				uint16_t pc = cpu->pc;
				int addr = cpu->executionState == SM83_CORE_FETCH ? ioTarget(core, cpu, &v, lo, hi) : -1;
				uint8_t ly = gb->memory.io[GB_REG_LY];
				uint32_t fr = core->frameCounter(core);
				core->step(core);
				if (addr >= 0) {
					fprintf(f, hi ? "%u %u %04x %04x %02x\n" : "%u %u %04x %02x %02x\n", fr, ly, pc, hi ? addr : addr & 0xFF, v);
				}
			}
			if (f) {
				fclose(f);
			} else {
				printf("error cannot open %s\n", path);
			}
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

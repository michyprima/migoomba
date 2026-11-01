/* Headless Game Boy reference renderer on mGBA's own GB core. Line commands on stdin, each answered
 * with output then "ok" (same protocol as gbaharness).
 * usage: gbref ROM.gb MODEL      MODEL: DMG, SGB or CGB (screenshots are always the 160x144 GB screen)
 *   frames N            run N GB frames
 *   frame               print the GB frame counter
 *   keys HEX            set GB keys (A=1 B=2 Sel=4 Start=8 R=10 L=20 U=40 D=80)
 *   shot PATH           write the 160x144 screen as PNG
 *   quit
 */
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
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

int main(int argc, char** argv) {
	if (argc < 3) {
		fprintf(stderr, "usage: %s ROM.gb DMG|SGB|CGB\n", argv[0]);
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

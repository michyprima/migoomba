/* Minimal PNG writer for mGBA video buffers (XBGR8 color_t), shared by the harness tools. */
#ifndef PNGWRITE_H
#define PNGWRITE_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <zlib.h>

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

/* pixels: w x h image, `stride` pixels per row */
static int writePNG(const char* path, const uint32_t* pixels, int w, int h, int stride) {
	size_t rawLen = h * (1 + w * 3);
	uint8_t* raw = malloc(rawLen);
	uint8_t* p = raw;
	for (int y = 0; y < h; ++y) {
		*p++ = 0;
		for (int x = 0; x < w; ++x) {
			uint32_t c = pixels[y * stride + x]; /* XBGR8 */
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
	uint8_t ihdr[13] = { w >> 24, w >> 16, w >> 8, w, h >> 24, h >> 16, h >> 8, h, 8, 2, 0, 0, 0 };
	chunk(f, "IHDR", ihdr, 13);
	chunk(f, "IDAT", z, zLen);
	chunk(f, "IEND", NULL, 0);
	fclose(f);
	free(raw);
	free(z);
	return 1;
}

#endif

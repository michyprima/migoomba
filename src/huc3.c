#include "includes.h"

//HuC-3 (Robopon, Pocket Family GB 2, ...): besides ROM/RAM banking, writing $0B/$0C/$0D to
//$0000-$1FFF makes $A000 a port to a 4-bit controller with a clock (mGBA's model):
//  mode $0B: a write sets the command (high nibble) and its argument (low nibble)
//  mode $0C: a read returns $80 | the command, with a read's result in the low nibble
//  mode $0D: a write runs the command; a read returns $FF (ready)
//Commands work on 256 nibbles: 1x reads the nibble at the index (then index+1), 3x writes x there
//(then index+1), 4x/5x set the index's low/high nibble, 60 copies the clock (minutes 10-12, days
//13-15) to 00-05, 61 copies 00-05 to the clock. The clock counts emulated frames; it is not kept
//across power-offs.

#define HUC3_MODE_IN 0x0B
#define HUC3_MODE_OUT 0x0C
#define HUC3_MODE_COMMIT 0x0D
#define FRAMES_PER_MINUTE 3584		//59.73 Hz

EWRAM_BSS u8 huc3_regs[256];
EWRAM_BSS u8 huc3_index;
EWRAM_BSS u8 huc3_value;
EWRAM_BSS u32 huc3_frame;			//frametotal when the clock was last brought up to date
EWRAM_BSS u8 huc3_started;			//huc3_frame is set (frametotal is reset after the mapper)

void huc3_reset(void)
{
	memset(huc3_regs, 0, sizeof(huc3_regs));
	huc3_index = 0;
	huc3_value = 0;
	huc3_started = 0;
}

static void huc3_update_clock(void)
{
	s32 frames = (s32)(frametotal - huc3_frame);
	if (!huc3_started || frames < 0)	//first use, or a loaded state put the frame count back
	{
		huc3_started = 1;
		huc3_frame = frametotal;
		return;
	}
	u32 minutes = frames / FRAMES_PER_MINUTE;
	if (minutes == 0)
	{
		return;
	}
	huc3_frame += minutes * FRAMES_PER_MINUTE;
	u32 m = huc3_regs[0x10] | huc3_regs[0x11] << 4 | huc3_regs[0x12] << 8;
	u32 d = huc3_regs[0x13] | huc3_regs[0x14] << 4 | huc3_regs[0x15] << 8;
	m += minutes;
	d += m / 1440;
	m %= 1440;
	huc3_regs[0x10] = m & 0xF;
	huc3_regs[0x11] = (m >> 4) & 0xF;
	huc3_regs[0x12] = (m >> 8) & 0xF;
	huc3_regs[0x13] = d & 0xF;
	huc3_regs[0x14] = (d >> 4) & 0xF;
	huc3_regs[0x15] = (d >> 8) & 0xF;
}

static void huc3_commit(void)
{
	int i;
	switch (huc3_value & 0x70)
	{
	case 0x10:	//read
		if ((huc3_index & 0xF8) == 0x10)
		{
			huc3_update_clock();
		}
		huc3_value = (huc3_value & 0xF0) | (huc3_regs[huc3_index] & 0xF);
		huc3_index++;
		break;
	case 0x30:	//write
		huc3_regs[huc3_index] = huc3_value & 0xF;
		huc3_index++;
		break;
	case 0x40:
		huc3_index = (huc3_index & 0xF0) | (huc3_value & 0xF);
		break;
	case 0x50:
		huc3_index = (huc3_index & 0x0F) | (huc3_value & 0xF) << 4;
		break;
	case 0x60:
		if ((huc3_value & 0xF) == 0)		//latch the clock
		{
			huc3_update_clock();
			for (i = 0; i < 6; i++)
			{
				huc3_regs[i] = huc3_regs[0x10 + i];
			}
		}
		else if ((huc3_value & 0xF) == 1)	//set the clock
		{
			for (i = 0; i < 6; i++)
			{
				huc3_regs[0x10 + i] = huc3_regs[i];
			}
			huc3_frame = frametotal;
			huc3_started = 1;
		}
		huc3_value = 0xE1;
		break;
	}
}

void huc3_write(int value, int mode)
{
	if (mode == HUC3_MODE_IN)
	{
		huc3_value = 0x80 | value;
	}
	else if (mode == HUC3_MODE_COMMIT)
	{
		huc3_commit();
	}
}

int huc3_read(int mode)
{
	if (mode == HUC3_MODE_IN || mode == HUC3_MODE_OUT)
	{
		return 0x80 | huc3_value;
	}
	return 0xFF;
}

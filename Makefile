MAKEFILE	:=	makefile

#---------------------------------------------------------------------------------
# Clear the implicit built in rules
#---------------------------------------------------------------------------------
.SUFFIXES:
#---------------------------------------------------------------------------------
ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment. export DEVKITARM=<path to>devkitARM)
endif


include $(DEVKITARM)/gba_rules

#--------
# Overrides for default rules

#%.o: %.s
#	@echo $(notdir $<)
#	$(CC) -MMD -MP -MF $(DEPSDIR)/$*.d -x assembler-with-cpp $(ASFLAGS) -c $< -o $@
#
#%.o: %.S
#	@echo $(notdir $<)
#	$(CC) -MMD -MP -MF $(DEPSDIR)/$*.d -x assembler-with-cpp $(ASFLAGS) -c $< -o $@

#OBJCOPY		:=	$(OBJCOPY) -R.pad

# We override the specs file because the default crt0 always clears EWRAM, and we don't want this.
# when DevKitArm changes the specs files, change the copy in the project too!

%_mb.elf:
	@echo linking multiboot CUSTOM
	@$(LD) $(LDFLAGS) -specs=../src/gba_mb_my.specs $(OFILES) $(LIBPATHS) $(LIBS) -o $@
%.elf:
	@echo linking cartridge CUSTOM
	@$(LD)  $(LDFLAGS) -specs=../src/gba_my.specs $(OFILES) $(LIBPATHS) $(LIBS) -o $@

#-------



#---------------------------------------------------------------------------------
# TARGET is the name of the output, if this ends with _mb generates a multiboot image
# BUILD is the directory where object files & intermediate files will be placed
# SOURCES is a list of directories containing source code
# INCLUDES is a list of directories containing extra header files
#---------------------------------------------------------------------------------
TARGET		:=	migoomba
BUILD		:=	build
SOURCES		:=	src
INCLUDES	:=

#---------------------------------------------------------------------------------
# options for code generation
#---------------------------------------------------------------------------------
ARCH	:=	-mthumb -mthumb-interwork

CFLAGS	:=	-g -Wall -Os\
			-mcpu=arm7tdmi -mtune=arm7tdmi\
 			-fomit-frame-pointer\
			-ffast-math \
			-std=c99 \
			$(ARCH)

CFLAGS	+=	$(INCLUDE)

CXXFLAGS := $(CFLAGS) \
 			-fno-rtti -fno-exceptions

ASFLAGS	:=	$(ARCH)
LDFLAGS	=	-g $(ARCH) -Wl,-Map,$(notdir $@).map

#---------------------------------------------------------------------------------
# path to tools - this can be deleted if you set the path in windows
#---------------------------------------------------------------------------------
export PATH		:=	$(DEVKITARM)/bin:$(PATH)

#---------------------------------------------------------------------------------
# any extra libraries we wish to link with the project
#---------------------------------------------------------------------------------
LIBS	:=	-lgba

#---------------------------------------------------------------------------------
# list of directories containing libraries, this must be the top level containing
# include and lib
#---------------------------------------------------------------------------------
LIBDIRS	:=	$(LIBGBA)

#---------------------------------------------------------------------------------
# no real need to edit anything past this point unless you need to add additional
# rules for different file extensions
#---------------------------------------------------------------------------------
ifneq ($(BUILD),$(notdir $(CURDIR)))
#---------------------------------------------------------------------------------

export OUTPUT	:=	$(CURDIR)/$(TARGET)

export VPATH	:=	$(foreach dir,$(SOURCES),$(CURDIR)/$(dir))
export PATH	:=	$(DEVKITARM)/bin:$(PATH)
export DEPSDIR	:=	$(CURDIR)/$(BUILD)

#---------------------------------------------------------------------------------
# automatically build a list of object files for our project
#---------------------------------------------------------------------------------
CFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
CPPFILES	:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))
SFILES		:=	all.s
DATA1		:=	font.lz77
DATA2		:=	fontpal.bin
DATA3		:=	#../goomba_mb.gba

#---------------------------------------------------------------------------------
# use CXX for linking C++ projects, CC for standard C
#---------------------------------------------------------------------------------
ifeq ($(strip $(CPPFILES)),)
#---------------------------------------------------------------------------------
	export LD	:=	$(CC)
#---------------------------------------------------------------------------------
else
#---------------------------------------------------------------------------------
	export LD	:=	$(CXX)
#---------------------------------------------------------------------------------
endif
#---------------------------------------------------------------------------------

export OFILES	:= $(CPPFILES:.cpp=.o) $(CFILES:.c=.o) $(SFILES:.s=.o) $(addsuffix .o,$(DATA1) $(DATA2) $(DATA3))

#---------------------------------------------------------------------------------
# build a list of include paths
#---------------------------------------------------------------------------------
export INCLUDE	:=	$(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
					$(foreach dir,$(LIBDIRS),-I$(dir)/include) \
					-I$(CURDIR)/$(BUILD)

#---------------------------------------------------------------------------------
# build a list of library paths
#---------------------------------------------------------------------------------
export LIBPATHS	:=	$(foreach dir,$(LIBDIRS),-L$(dir)/lib)

.PHONY: $(BUILD) clean

#---------------------------------------------------------------------------------
$(BUILD):
	@[ -d $@ ] || mkdir -p $@
	@make --no-print-directory -C $(BUILD) -f $(CURDIR)/$(MAKEFILE)

all	: $(BUILD)

#---------------------------------------------------------------------------------
# playable ROMs: the emulator followed by Game Boy ROMs, each padded (or cut) to the size its header
# declares, as the emulator finds the next game by that size; with several games it shows a menu
#   make game.gbc [OUT=name.gba]                -> game.gba (here)
#   make a.gbc b.gb c.gbc [OUT=name.gba]        -> compilation.gba, games in this order
# (make splits its arguments at spaces: rename such files, or cat migoomba.gba "my game.gbc" > out.gba)
#---------------------------------------------------------------------------------
ROMS	:=	$(filter %.gb %.gbc %.GB %.GBC,$(MAKECMDGOALS))
ifneq ($(ROMS),)
ROMOUT	:=	$(if $(OUT),$(OUT),$(if $(word 2,$(ROMS)),compilation.gba,$(basename $(notdir $(ROMS))).gba))
.PHONY: $(ROMS) romfile
$(ROMS): romfile
	@:
romfile: $(BUILD)
	@out="$(ROMOUT)"; cp $(TARGET).gba "$$out.tmp"; n=0; \
	for f in $(ROMS); do \
		if [ ! -f "$$f" ]; then rm "$$out.tmp"; echo "$$f: no such file"; exit 1; fi; \
		if [ "$$(od -An -tx1 -j 260 -N4 "$$f" | tr -d ' \n')" != "ceed6666" ]; then \
			rm "$$out.tmp"; echo "$$f: not a Game Boy ROM (no header)"; exit 1; fi; \
		code=$$(od -An -tu1 -j 328 -N1 "$$f" | tr -d ' \n'); \
		if [ "$$code" -gt 8 ]; then rm "$$out.tmp"; echo "$$f: unsupported ROM size code $$code"; exit 1; fi; \
		size=$$((32768 << code)); have=$$(wc -c < "$$f" | tr -d ' '); \
		head -c $$size "$$f" >> "$$out.tmp"; \
		if [ $$have -lt $$size ]; then head -c $$((size - have)) /dev/zero >> "$$out.tmp"; fi; \
		n=$$((n + 1)); \
	done; \
	total=$$(wc -c < "$$out.tmp" | tr -d ' '); \
	if [ $$total -gt 33554432 ]; then rm "$$out.tmp"; echo "too big for a GBA cart: $$total bytes, 32MB at most"; exit 1; fi; \
	mv "$$out.tmp" "$$out"; if [ $$n -gt 1 ]; then echo "built $$out: $$n games"; else echo "built $$out"; fi
endif
#---------------------------------------------------------------------------------
semiclean:
	@echo deleting intermediate files...
	@rm -fr $(BUILD) $(TARGET).elf

clean: semiclean
	@echo deleting main binary
	@rm -f $(TARGET).gba

#---------------------------------------------------------------------------------
else

DEPENDS	:=	$(OFILES:.o=.d)

%.lz77.o	:	%.lz77
	@echo $(notdir $<)
	@$(bin2o)

%.bin.o	:	%.bin
	@echo $(notdir $<)
	@$(bin2o)

%.gba.o	:	%.gba
	@echo $(notdir $<)
	@$(bin2o)


#---------------------------------------------------------------------------------
# main targets
#---------------------------------------------------------------------------------
$(OUTPUT).gba	:	$(OUTPUT).elf

$(OUTPUT).elf	:	$(OFILES)	gba_crt0_my.o

-include $(DEPENDS)

#replacement rule for gbafix
#---------------------------------------------------------------------------------
%.gba: %.elf
	@$(OBJCOPY) -O binary $< $@
	@echo CUSTOM built ... $(notdir $@)
	@echo gbafix $@ -t GOOMBA COLOR -c GMBC
	@gbafix $@ "-tGOOMBA COLOR" "-cGMBC"



#---------------------------------------------------------------------------------
endif
#---------------------------------------------------------------------------------
